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
    # The tier that floor requires. ``tier`` may sit above it (a backend chose
    # higher); the router refuses only when *this* exceeds the cap, and clamps
    # the rest. ``None`` whenever ``floor`` is ``None``.
    floor_tier: Tier | None = None


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
    # What each deterministic safety floor forces, excluding heuristic uplift.
    LEGAL_FLOOR_TIER = Tier.T2
    VIP_FLOOR_TIER = Tier.T2
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
            # The rules fallback keeps its floor (verifier finding on #251): an
            # abstained legal prompt is refused below T2, not clamped.
            return JudgmentDecision(
                tier=rules.tier,
                confidence=rules.confidence,
                reason=f"{answer.backend} abstained ({answer.abstain_reason}); rules fallback: {rules.reason}",
                floor=rules.floor,
                floor_tier=rules.floor_tier,
            )
        backend_tier = Tier(answer.choice)
        backend_reason = f"{answer.backend} choice (calibrated={answer.calibrated})"
        # Whether a safety floor applies is a property of the request, not of
        # where the backend's choice landed. A T2 or T3 choice on a legal prompt
        # carries the floor too, so a T1 cap refuses instead of clamping under it.
        safety = self.safety_floor_for(req)
        floor_name = safety[1] if safety else None
        floor_tier_required = safety[0] if safety else None
        floored = self.apply_rules_floor(req, backend_tier)
        if floored is None:
            return JudgmentDecision(
                tier=backend_tier,
                confidence=answer.confidence,
                reason=backend_reason,
                floor=floor_name,
                floor_tier=floor_tier_required,
            )
        floor_tier, floor_reason = floored
        return JudgmentDecision(
            tier=floor_tier,
            confidence=answer.confidence,
            reason=(
                f"{answer.backend} choice {backend_tier.value} (calibrated={answer.calibrated}) "
                f"overridden by {floor_reason}: {floor_tier.value}"
            ),
            floor=floor_name,
            floor_tier=floor_tier_required,
        )

    def safety_floor_for(self, req: JudgmentRequest) -> tuple[Tier, str] | None:
        """The safety floor the request's content triggers, whatever tier was chosen.

        ``(Tier.T2, LEGAL_FLOOR)`` when the content has legal/financial terms,
        including alongside ``birthday`` (the birthday quality floor may raise
        the tier further, but never removes the legal floor). ``None`` for the
        T0 pin (those requests never reach a model) and for plain content. The
        VIP floor is rules-only and is stamped by ``rules_decide``.
        """
        req_type = req.request_type.lower().strip()
        if req_type in {"embedding", "intent_classify", "sentiment"}:
            return None
        if self._contains_legal_terms(req.content):
            return self.LEGAL_FLOOR_TIER, self.LEGAL_FLOOR
        return None

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
        elif self._contains_legal_terms(req.content):
            floor, name = self.LEGAL_FLOOR_TIER, self.LEGAL_FLOOR

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
            # Birthday is a quality floor and keeps the clamp on its own; but a
            # legal term in the same prompt still carries the T2 safety floor,
            # so a T1 cap refuses rather than serving the loan review at T1.
            if self._contains_legal_terms(req.content):
                return JudgmentDecision(
                    tier=Tier.T3,
                    confidence=0.95,
                    reason="birthday rapport quality path; financial/legal terms detected",
                    floor=self.LEGAL_FLOOR,
                    floor_tier=self.LEGAL_FLOOR_TIER,
                )
            return JudgmentDecision(tier=Tier.T3, confidence=0.95, reason="birthday rapport quality path")

        if self._contains_legal_terms(req.content):
            return JudgmentDecision(
                tier=Tier.T2,
                confidence=0.92,
                reason="financial/legal terms detected",
                floor=self.LEGAL_FLOOR,
                floor_tier=self.LEGAL_FLOOR_TIER,
            )

        tier = Tier.T1 if req.context_size <= 2500 else Tier.T2

        if req.prior_tier_failures > 0 and tier == Tier.T1:
            tier = Tier.T2

        if req.is_vip:
            tier = Tier.T2 if tier == Tier.T1 else Tier.T3
            # The VIP safety floor is the rule's minimum, T2 (the bump from the
            # T1 base), never the heuristic context-size/retry uplift on top of
            # it. A large VIP prompt in a max_tier=T2 node is clamped to T2, not
            # refused; only a cap below T2 refuses (verifier finding 2, #251).
            return JudgmentDecision(
                tier=tier,
                confidence=0.7,
                reason="heuristic routing fallback",
                floor=self.VIP_FLOOR,
                floor_tier=self.VIP_FLOOR_TIER,
            )

        return JudgmentDecision(tier=tier, confidence=0.7, reason="heuristic routing fallback")

    def _contains_legal_terms(self, text: str) -> bool:
        """Token-level, case-insensitive match of ``LEGAL_TERMS`` (H2-LEGAL-CLAMP, #251).

        ``text`` is split into tokens on every non-alphanumeric character
        (including ``_``) and on camelCase/PascalCase/letter-digit boundaries,
        then lowercased; see ``legal_tokens``. A single-word term matches a
        token, a phrase matches consecutive tokens, and the last token may carry
        a plain plural ``s``. So ``spa`` matches ``SPA``, ``spa_form`` and
        ``SpaForm`` but never ``space``; ``legal`` never matches ``illegal``;
        ``contract`` never matches ``contractor``; ``loan_agreement``,
        ``LoanAgreement`` and ``stamp-duty`` all match. Pass the original-case
        text so camelCase boundaries survive; lowercased text still matches
        separator-delimited terms.
        """
        tokens = legal_tokens(text)
        if not tokens:
            return False
        phrases = _term_phrases(tuple(self.LEGAL_TERMS))
        return any(_matches_at(tokens, i, phrase) for phrase in phrases for i in range(len(tokens)))

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


# Order matters. An all-caps run followed by a lowercase ``s`` is a pluralised
# acronym (``SPAs``, ``NRICs``); an all-caps run followed by Capital+lower is an
# acronym before a PascalCase word (``SPAForm`` -> ``SPA``, ``Form``); then a
# capitalised or lowercase word, a bare caps run, a digit run, and any other
# (non-ASCII) letter run.
_TOKEN_RE = re.compile(r"[A-Z]{2,}s(?![a-z])|[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+|[^\W\d_]+")


def legal_tokens(text: str) -> list[str]:
    """Lowercased word tokens of ``text`` for legal-term matching.

    Splits on every non-alphanumeric character (whitespace, punctuation, ``_``,
    ``-``, ``.``, JSON quotes) and on camelCase, PascalCase, acronym and
    letter/digit boundaries: ``loan_agreement``, ``LoanAgreement``,
    ``stampDutyCalc``, ``SPAForm``, ``loan2024`` each yield their words.
    """
    return [t.lower() for t in _TOKEN_RE.findall(text)]


_TERM_PHRASES: dict[tuple[str, ...], tuple[tuple[str, ...], ...]] = {}


def _term_phrases(terms: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    """Each term as its token sequence (``"stamp duty"`` -> ``("stamp", "duty")``), cached."""
    phrases = _TERM_PHRASES.get(terms)
    if phrases is None:
        phrases = tuple(p for p in (tuple(legal_tokens(t)) for t in terms) if p)
        _TERM_PHRASES[terms] = phrases
    return phrases


def _matches_at(tokens: list[str], i: int, phrase: tuple[str, ...]) -> bool:
    """``phrase`` occupies ``tokens[i:]``; the last token may add a plural ``s``."""
    n = len(phrase)
    if i + n > len(tokens):
        return False
    if any(tokens[i + k] != phrase[k] for k in range(n - 1)):
        return False
    last = tokens[i + n - 1]
    return last == phrase[-1] or last == phrase[-1] + "s"
