"""F3 — warehouse intent + sentiment classify (T0/T1 local, optional GPU model)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from packs.dms.security.prompt_harness import HarnessResult, secure_for_prompt

# Warehouse/logistics intent set (BUILD_PLAN F3)
WAREHOUSE_INTENTS: tuple[str, ...] = (
    "check_stock",
    "order_status",
    "request_quote",
    "schedule_pickup",
    "report_issue",
    "update_address",
    "complaint",
    "chit_chat",
    "other",
)

_INTENT_RULES: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("check_stock", re.compile(r"(?i)\b(stock|inventory|qty|quantity|available|in\s+stock)\b"), 0.85),
    ("order_status", re.compile(r"(?i)\b(where\s+is|track|status|shipment|delivery|order\s+#)\b"), 0.82),
    ("request_quote", re.compile(r"(?i)\b(quote|pricing|price|how\s+much|cost\s+for)\b"), 0.80),
    ("schedule_pickup", re.compile(r"(?i)\b(pick\s*up|collect|schedule|arrange\s+collection)\b"), 0.78),
    ("report_issue", re.compile(r"(?i)\b(damaged|missing|wrong|broken|issue|problem|defect)\b"), 0.84),
    ("update_address", re.compile(r"(?i)\b(change\s+address|new\s+address|deliver\s+to)\b"), 0.80),
    ("complaint", re.compile(r"(?i)\b(complain|unacceptable|terrible|refund|dispute|angry)\b"), 0.83),
    ("chit_chat", re.compile(r"(?i)^(hi|hello|hey|thanks|thank\s+you|good\s+morning)\b"), 0.70),
)

_NEGATIVE = re.compile(r"(?i)\b(angry|furious|terrible|awful|unacceptable|refund|dispute|late|broken)\b")
_POSITIVE = re.compile(r"(?i)\b(thanks|great|perfect|excellent|appreciate|good\s+job)\b")


@dataclass(frozen=True, slots=True)
class ClassifyResult:
    intent: str
    sentiment: float  # -1..+1
    confidence: float
    blocked: bool
    block_reason: str | None
    language_mix: dict[str, float]
    psychological_state: str  # for closer persona routing


def _psychological_state(sentiment: float, intent: str, scam_risk: float) -> str:
    if scam_risk >= 0.7:
        return "suspicious"
    if intent == "complaint" or sentiment < -0.5:
        return "frustrated"
    if intent in ("request_quote", "schedule_pickup") and sentiment >= 0:
        return "ready_to_buy"
    if intent == "chit_chat":
        return "casual"
    if sentiment > 0.3:
        return "positive"
    return "neutral"


def _blocked_result(block_reason: str | None) -> ClassifyResult:
    return ClassifyResult(
        intent="other",
        sentiment=0.0,
        confidence=1.0,
        blocked=True,
        block_reason=block_reason,
        language_mix={"en": 1.0},
        psychological_state="suspicious",
    )


def _classify_safe_text(safe: str, harness: HarnessResult) -> ClassifyResult:
    best_intent = "other"
    best_score = 0.45
    for name, pattern, base in _INTENT_RULES:
        if pattern.search(safe):
            if base > best_score:
                best_score = base
                best_intent = name

    neg = len(_NEGATIVE.findall(safe))
    pos = len(_POSITIVE.findall(safe))
    if neg > pos:
        sentiment = max(-1.0, -0.3 - 0.2 * neg)
    elif pos > neg:
        sentiment = min(1.0, 0.3 + 0.2 * pos)
    else:
        sentiment = 0.0

    return ClassifyResult(
        intent=best_intent,
        sentiment=round(sentiment, 3),
        confidence=round(best_score, 3),
        blocked=False,
        block_reason=None,
        language_mix={"en": 1.0},
        psychological_state=_psychological_state(sentiment, best_intent, harness.scam_risk),
    )


def classify_heuristic(text: str) -> ClassifyResult:
    harness = secure_for_prompt(text, block_injection=True, block_scam=False)
    if harness.blocked:
        return _blocked_result(harness.block_reason)
    return _classify_safe_text(harness.safe_text, harness)


def classify(text: str) -> ClassifyResult:
    """Public API — security harness first, then optional local model or heuristic."""
    harness = secure_for_prompt(text, block_injection=True, block_scam=False)
    if harness.blocked:
        return _blocked_result(harness.block_reason)

    safe = harness.safe_text
    try:
        from CortexOS.nlp.local_inference import classify_with_model

        return classify_with_model(safe)
    except Exception:
        return _classify_safe_text(safe, harness)
