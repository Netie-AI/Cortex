from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .tiers import Tier


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

    def __init__(
        self,
        decision_backend: Any | None = None,
        *,
        abstain_threshold: float | None = None,
        check_order: bool = True,
    ) -> None:
        self.decision_backend = decision_backend
        self.abstain_threshold = abstain_threshold
        self.check_order = check_order

    @classmethod
    def from_env(cls) -> JudgmentModel:
        """Rules-v0 unless ``CORTEX_KEV_URL`` names a loopback kev server."""
        from CortexOS.decision.backends import KEV_URL_ENV, KevHttpBackend

        url = os.environ.get(KEV_URL_ENV, "").strip()
        if not url:
            return cls()
        return cls(decision_backend=KevHttpBackend(url))

    def decide(self, req: JudgmentRequest) -> JudgmentDecision:
        if self.decision_backend is None:
            return self.rules_decide(req)
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
        return JudgmentDecision(
            tier=Tier(answer.choice),
            confidence=answer.confidence,
            reason=f"{answer.backend} choice (calibrated={answer.calibrated})",
        )

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
            return JudgmentDecision(tier=Tier.T2, confidence=0.92, reason="financial/legal terms detected")

        tier = Tier.T1 if req.context_size <= 2500 else Tier.T2

        if req.prior_tier_failures > 0 and tier == Tier.T1:
            tier = Tier.T2

        if req.is_vip:
            tier = Tier.T2 if tier == Tier.T1 else Tier.T3

        return JudgmentDecision(tier=tier, confidence=0.7, reason="heuristic routing fallback")

    def _contains_legal_terms(self, text: str) -> bool:
        return any(term in text for term in self.LEGAL_TERMS)

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
