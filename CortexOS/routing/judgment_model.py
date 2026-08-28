from collections.abc import Iterable
from dataclasses import dataclass

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
    V1 can add DistilBERT probabilities and map them to tiers.
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

    def decide(self, req: JudgmentRequest) -> JudgmentDecision:
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
