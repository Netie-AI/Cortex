"""Multitask sentiment + intent (fine-tuned XLM-R placeholder). Ships stub until corpus is labelled."""

from __future__ import annotations

from dataclasses import dataclass

INTENT_CLASSES: tuple[str, ...] = (
    "ready_to_view",
    "asking_price",
    "asking_specs",
    "objection_price",
    "objection_location",
    "objection_unit_quality",
    "ready_to_close",
    "ghosting",
    "off_topic",
)


@dataclass(slots=True)
class SentimentIntentResult:
    sentiment: float  # -1..+1
    intent: str  # one of INTENT_CLASSES
    language_mix: dict[str, float]  # e.g. {"en": 0.6, "ms": 0.3, "zh": 0.1}
    confidence: float


class SentimentIntentModel:
    """
    Stub inference surface for Phase 3 wiring (closer gate on ``ready_to_close``).
    Replace ``predict()`` with ONNX/torchweights once the ~50-conv corpus is labelled.
    """

    def predict(self, text: str) -> SentimentIntentResult:  # noqa: ARG002
        del text
        return SentimentIntentResult(
            sentiment=0.0,
            intent="off_topic",
            language_mix={"en": 1.0, "ms": 0.0, "zh": 0.0},
            confidence=0.5,
        )
