"""Decision backends: rules (wraps the existing JudgmentModel) and kev over HTTP.

``KevHttpBackend`` only ever talks to a loopback host. Keys live in OpenVault, so
a non-loopback URL is refused at construction rather than sent a request. Any
HTTP or parse failure becomes an explicit degraded ``RawDecision``; nothing here
silently falls back to rules.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

from CortexOS.routing.tiers import Tier

from .models import ChoiceQuestion, NoulQuestion, Question, RawDecision, ScoreQuestion

KEV_URL_ENV = "CORTEX_KEV_URL"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
TIER_OPTIONS: tuple[tuple[str, str], ...] = (
    (Tier.T0.value, "deterministic local tooling: embeddings, intent, sentiment"),
    (Tier.T1.value, "small self-hosted model for short, ordinary requests"),
    (Tier.T2.value, "larger self-hosted model for long context or legal/financial content"),
    (Tier.T3.value, "hosted frontier model for quality-critical or VIP paths"),
)


def tier_question() -> ChoiceQuestion:
    return ChoiceQuestion("Which model tier should serve this request?", TIER_OPTIONS)


@runtime_checkable
class DecisionBackend(Protocol):
    name: str

    def evaluate(self, question: Question, state: Any) -> RawDecision: ...


class RulesBackend:
    """Expresses the rules-v0 JudgmentModel as a choice over T0..T3.

    Rules confidence is a hand-written constant, not a probability, so every
    answer is stamped ``calibrated=False``. Only the tier question is supported.
    ``state`` must be a ``JudgmentRequest``.
    """

    name = "rules-v0"

    def __init__(self, judgment_model: Any | None = None) -> None:
        if judgment_model is None:
            from CortexOS.routing.judgment_model import JudgmentModel

            judgment_model = JudgmentModel()
        self._jm = judgment_model

    def evaluate(self, question: Question, state: Any) -> RawDecision:
        if not isinstance(question, ChoiceQuestion):
            return RawDecision.failure(self.name, "rules backend only answers choice questions")
        labels = question.labels
        if set(labels) != {t.value for t in Tier}:
            return RawDecision.failure(self.name, "rules backend only answers the tier question")
        decision = self._jm.rules_decide(self._request(state))
        p = float(decision.confidence)
        rest = (1.0 - p) / (len(labels) - 1)
        scores = tuple(p if name == decision.tier.value else rest for name in labels)
        return RawDecision(scores=scores, is_logits=False, calibrated=False, backend=self.name)

    @staticmethod
    def _request(state: Any) -> Any:
        """Accept a ``JudgmentRequest`` or the dict ``JudgmentModel._state_for`` builds."""
        if not isinstance(state, dict):
            return state
        from CortexOS.routing.judgment_model import JudgmentRequest

        return JudgmentRequest(
            request_type=str(state.get("request_type", "")),
            content=str(state.get("content", "")),
            context_size=int(state.get("context_size", 0)),
            prior_tier_failures=int(state.get("prior_tier_failures", 0)),
            user_tier_budget=Tier(state.get("user_tier_budget", Tier.T2.value)),
            is_vip=bool(state.get("is_vip", False)),
        )


def is_loopback_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or not host:
        return False
    return host in LOOPBACK_HOSTS or host.startswith("127.")


class KevHttpBackend:
    """POSTs kev's ``/v1/systemone`` shape to a local kev server."""

    name = "kev-http"
    QUESTION_ID = "q"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        model: str = "kev-latest",
        timeout_s: float = 5.0,
        server_calibrated: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        url = (base_url or os.environ.get(KEV_URL_ENV, "")).strip()
        if not url:
            raise ValueError(f"KevHttpBackend needs a base URL ({KEV_URL_ENV})")
        if not is_loopback_url(url):
            raise ValueError(f"KevHttpBackend refuses non-loopback URL {url!r}; keys live in OpenVault")
        self.base_url = url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.server_calibrated = server_calibrated
        self._client = client

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/v1/systemone"

    def evaluate(self, question: Question, state: Any) -> RawDecision:
        body = {
            "state": state,
            "model": self.model,
            "questions": {self.QUESTION_ID: question.to_kev()},
        }
        try:
            if self._client is not None:
                resp = self._client.post(self.endpoint, json=body, timeout=self.timeout_s)
            else:
                resp = httpx.post(self.endpoint, json=body, timeout=self.timeout_s)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPStatusError as exc:
            return RawDecision.failure(self.name, f"http {exc.response.status_code}")
        except httpx.HTTPError as exc:
            return RawDecision.failure(self.name, f"transport: {type(exc).__name__}")
        except ValueError as exc:
            return RawDecision.failure(self.name, f"invalid json: {exc}")
        try:
            scores = self._parse(question, payload)
        except (KeyError, TypeError, ValueError) as exc:
            return RawDecision.failure(self.name, f"parse: {exc}")
        return RawDecision(
            scores=scores, is_logits=False, calibrated=self.server_calibrated, backend=self.name
        )

    def _parse(self, question: Question, payload: Any) -> tuple[float, ...]:
        if not isinstance(payload, dict):
            raise ValueError("response is not an object")
        answer = payload["answers"][self.QUESTION_ID]
        if isinstance(question, NoulQuestion):
            p = float(answer["noul"])
            if not 0.0 <= p <= 1.0:
                raise ValueError(f"noul out of range: {p}")
            return (1.0 - p, p)
        probs = answer["probabilities"]
        if not isinstance(probs, dict):
            raise ValueError("probabilities must be an object")
        if isinstance(question, (ChoiceQuestion, ScoreQuestion)):
            missing = [k for k in question.labels if k not in probs]
            if missing:
                raise KeyError(f"probabilities missing {missing}")
            scores = tuple(float(probs[k]) for k in question.labels)
            if any(s < 0.0 for s in scores) or sum(scores) <= 0.0:
                raise ValueError("probabilities must be non-negative and sum > 0")
            return scores
        raise ValueError(f"unsupported question type {type(question).__name__}")
