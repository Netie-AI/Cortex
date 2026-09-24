from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .tiers import TIER_ORDER, Tier


@dataclass(slots=True)
class JudgmentRequest:
    request_type: str
    content: str
    context_size: int = 0
    prior_tier_failures: int = 0
    user_tier_budget: Tier = Tier.T2
    is_vip: bool = False


@dataclass(slots=True)
class JudgmentDecision:
    tier: Tier
    confidence: float
    reason: str
    # H2-LEGAL-CLAMP (#251): the deterministic safety floor that produced
    # ``tier``, or ``None`` when the tier came from a heuristic or a backend.
    # ``ModelRouter`` refuses (instead of clamping) when a floored tier exceeds
    # the node's ``max_tier``. One of ``JudgmentModel.SAFETY_FLOORS``.
    floor: str | None = None


class JudgmentModel:
    """
    Rules-first judgment model.

    Opt-in calibrated path: pass ``decision_backend`` (a ``CortexOS.decision``
    backend) or set ``CORTEX_KEV_URL`` and construct via ``from_env()``. With no
    backend, ``decide()`` is the unchanged rules-v0 cascade.
    """

    LEGAL_TERMS: tuple[str, ...] = (
        "spa",
        "legal",
        "stamp duty",
        "agreement",
        "contract",
        "loan",
        "tenure",
        "nric",
        "rpgt",
    )

    LEGAL_FLOOR = "legal/financial floor"
    VIP_FLOOR = "vip floor"
    # Floors that must never be served below. A DSL ``max_tier`` under one of
    # these is refused by ``ModelRouter.route`` (H2-LEGAL-CLAMP, #251). The
    # birthday quality floor and the heuristic context-size tier keep the clamp.
    SAFETY_FLOORS: frozenset[str] = frozenset({LEGAL_FLOOR, VIP_FLOOR})

    def __init__(
        self,
        decision_backend: Any | None = None,
        *,
        abstain_threshold: float | None = None,
        check_order: bool = True,
        shadow: Any | None = None,
    ) -> None:
        self.decision_backend = decision_backend
        self.abstain_threshold = abstain_threshold
        self.check_order = check_order
        # KEV-SHADOW: a ``CortexOS.decision.shadow.ShadowEvaluator`` that is asked
        # beside the rules and never serves. Only consulted when no serving
        # backend is configured.
        self.shadow = shadow

    @classmethod
    def from_env(cls) -> JudgmentModel:
        """Rules-v0 unless ``CORTEX_KEV_URL`` names a loopback kev server.

        With ``CORTEX_KEV_SHADOW=1`` as well, the rules keep serving and kev is
        evaluated beside them (KEV-SHADOW); the served decision is unchanged.
        """
        from CortexOS.decision.backends import KEV_URL_ENV, KevHttpBackend

        url = os.environ.get(KEV_URL_ENV, "").strip()
        if not url:
            return cls()
        backend = KevHttpBackend(url)
        from CortexOS.decision.shadow import ShadowEvaluator, shadow_enabled

        if shadow_enabled():
            return cls(shadow=ShadowEvaluator(backend))
        return cls(decision_backend=backend)

    def decide(self, req: JudgmentRequest) -> JudgmentDecision:
        if self.decision_backend is None:
            rules = self.rules_decide(req)
            if self.shadow is not None:
                try:
                    self.shadow.observe(self._state_for(req), rules.tier)
                except Exception:  # shadow is observation only; never touches the served answer
                    pass
            return rules
        from CortexOS.decision.backends import tier_question
        from CortexOS.decision.decide import decide

        answer = decide(
            tier_question(),
            self._state_for(req),
            self.decision_backend,
            abstain_threshold=self.abstain_threshold,
            check_order=self.check_order,
        )
        if answer.abstain or answer.choice is None:
            rules = self.rules_decide(req)
            return JudgmentDecision(
                tier=rules.tier,
                confidence=rules.confidence,
                reason=f"{answer.backend} abstained ({answer.abstain_reason}); rules fallback: {rules.reason}",
            )
        backend_tier = Tier(answer.choice)
        backend_reason = f"{answer.backend} choice (calibrated={answer.calibrated})"
        floored = self.apply_rules_floor(req, backend_tier)
        if floored is None:
            return JudgmentDecision(
                tier=backend_tier,
                confidence=answer.confidence,
                reason=backend_reason,
            )
        floor_tier, floor_reason = floored
        return JudgmentDecision(
            tier=floor_tier,
            confidence=answer.confidence,
            reason=(
                f"{answer.backend} choice {backend_tier.value} (calibrated={answer.calibrated}) "
                f"overridden by {floor_reason}: {floor_tier.value}"
            ),
            floor=floor_reason if floor_reason in self.SAFETY_FLOORS else None,
        )

    def apply_rules_floor(self, req: JudgmentRequest, chosen: Tier) -> tuple[Tier, str] | None:
        """Deterministic rules trump any backend choice.

        Returns ``(tier, override_name)`` when the choice must change, else
        ``None``. The floors only ever raise a tier; the one pin (deterministic
        low-tier tasks) forces T0 because those requests never reach a model.
        Mirrors the first three branches of ``rules_decide`` so the two paths
        cannot drift apart.
        """
        req_type = req.request_type.lower().strip()
        text = req.content.lower()

        if req_type in {"embedding", "intent_classify", "sentiment"}:
            if chosen == Tier.T0:
                return None
            return Tier.T0, "deterministic low-tier pin"

        floor: Tier | None = None
        name = ""
        if "birthday" in req_type or "birthday" in text:
            floor, name = Tier.T3, "birthday quality floor"
        elif self._contains_legal_terms(text):
            floor, name = Tier.T2, self.LEGAL_FLOOR

        if floor is None or TIER_ORDER[chosen] >= TIER_ORDER[floor]:
            return None
        return floor, name

    @staticmethod
    def _state_for(req: JudgmentRequest) -> dict[str, Any]:
        return {
            "request_type": req.request_type,
            "content": req.content,
            "context_size": req.context_size,
            "prior_tier_failures": req.prior_tier_failures,
            "user_tier_budget": req.user_tier_budget.value,
            "is_vip": req.is_vip,
        }

    def rules_decide(self, req: JudgmentRequest) -> JudgmentDecision:
        """The rules-v0 cascade. Confidence here is a constant, not a probability."""
        req_type = req.request_type.lower().strip()
        text = req.content.lower()

        if req_type in {"embedding", "intent_classify", "sentiment"}:
            return JudgmentDecision(tier=Tier.T0, confidence=0.99, reason="deterministic low-tier task")

        if "birthday" in req_type or "birthday" in text:
            return JudgmentDecision(tier=Tier.T3, confidence=0.95, reason="birthday rapport quality path")

        if self._contains_legal_terms(text):
            return JudgmentDecision(
                tier=Tier.T2, confidence=0.92, reason="financial/legal terms detected", floor=self.LEGAL_FLOOR
            )

        tier = Tier.T1 if req.context_size <= 2500 else Tier.T2

        if req.prior_tier_failures > 0 and tier == Tier.T1:
            tier = Tier.T2

        if req.is_vip:
            tier = Tier.T2 if tier == Tier.T1 else Tier.T3
            return JudgmentDecision(
                tier=tier, confidence=0.7, reason="heuristic routing fallback", floor=self.VIP_FLOOR
            )

        return JudgmentDecision(tier=tier, confidence=0.7, reason="heuristic routing fallback")

    def _contains_legal_terms(self, text: str) -> bool:
        """Whole-word, case-insensitive match of ``LEGAL_TERMS`` (H2-LEGAL-CLAMP, #251).

        ``spa`` is the Sale and Purchase Agreement acronym as a word, never the
        inside of ``space``; ``legal`` never matches ``illegal``; ``contract``
        never matches ``contractor``. A plain plural (``contracts``) still
        matches. Multi-word phrases match across any run of whitespace.
        """
        return _legal_terms_pattern(tuple(self.LEGAL_TERMS)).search(text) is not None

    @staticmethod
    def big_api_request_types() -> Iterable[str]:
        return (
            "compliance_final_check",
            "birthday_rapport",
            "complex_negotiation_drafter",
            "free_text_query_parser",
            "listing_copy_polisher",
            "eval_judge",
        )


_LEGAL_PATTERNS: dict[tuple[str, ...], re.Pattern[str]] = {}


def _legal_terms_pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    """Compile ``terms`` into one word-bounded alternation, cached per term tuple.

    A term is bounded by anything that is not a word character, so ``spa``
    does not match ``space``, ``legal`` does not match ``illegal``, ``loan``
    does not match ``loaner`` and ``contract`` does not match ``contractor``.
    Hyphenated compounds such as ``sub-contract`` still match (fail closed).
    Whitespace inside a phrase matches any whitespace run, so ``stamp  duty``
    still matches ``stamp duty``.
    """
    pattern = _LEGAL_PATTERNS.get(terms)
    if pattern is None:
        alternatives = "|".join(re.escape(t.strip().lower()).replace("\\ ", r"\s+") for t in terms if t.strip())
        pattern = re.compile(rf"(?<!\w)(?:{alternatives})s?(?!\w)", re.IGNORECASE)
        _LEGAL_PATTERNS[terms] = pattern
    return pattern
