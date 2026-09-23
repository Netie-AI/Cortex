"""``decide()``: run a backend, calibrate, threshold, optionally check option order."""

from __future__ import annotations

import os
from typing import Any

from .backends import DecisionBackend
from .calibration import choice_confidence, softmax
from .models import (
    ChoiceQuestion,
    DecisionAnswer,
    NoulQuestion,
    Question,
    RawDecision,
    ScoreQuestion,
)

ABSTAIN_THRESHOLD_ENV = "CORTEX_DECISION_ABSTAIN_THRESHOLD"
DEFAULT_ABSTAIN_THRESHOLD = 0.5


def default_abstain_threshold() -> float:
    raw = os.environ.get(ABSTAIN_THRESHOLD_ENV, "").strip()
    if not raw:
        return DEFAULT_ABSTAIN_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_ABSTAIN_THRESHOLD
    return min(1.0, max(0.0, value))


def _probabilities(raw: RawDecision, temperature: float | None) -> tuple[list[float], bool, float | None]:
    if raw.is_logits:
        t = temperature if temperature is not None else 1.0
        return softmax(raw.scores, t), raw.calibrated or temperature is not None, t
    total = sum(raw.scores)
    return [s / total for s in raw.scores], raw.calibrated, None


def _evaluate(backend: DecisionBackend, question: Question, state: Any) -> RawDecision:
    raw = backend.evaluate(question, state)
    if raw.degraded:
        return raw
    if len(raw.scores) != len(question.labels):
        return RawDecision.failure(
            raw.backend, f"backend returned {len(raw.scores)} scores for {len(question.labels)} labels"
        )
    return raw


def decide(
    question: Question,
    state: Any,
    backend: DecisionBackend,
    *,
    temperature: float | None = None,
    abstain_threshold: float | None = None,
    check_order: bool = False,
) -> DecisionAnswer:
    """Answer ``question`` about ``state`` with ``backend``.

    - ``temperature`` is applied only when the backend supplies logits.
    - ``abstain=True`` when confidence < threshold (env ``CORTEX_DECISION_ABSTAIN_THRESHOLD``).
    - ``check_order`` re-asks a choice question with reversed option order; if the
      argmax moves, the answer is ``order_sensitive`` and abstains.
    - Backend failure gives a degraded, abstaining answer with the cause.
    """
    threshold = default_abstain_threshold() if abstain_threshold is None else abstain_threshold
    raw = _evaluate(backend, question, state)
    if raw.degraded:
        return DecisionAnswer.degraded_answer(question.type, raw.backend, raw.cause or "unknown")

    probs, calibrated, applied_t = _probabilities(raw, temperature)
    labels = question.labels
    prob_map = {label: p for label, p in zip(labels, probs, strict=True)}
    confidence = choice_confidence(probs)
    argmax = max(range(len(probs)), key=lambda i: probs[i])

    order_sensitive = False
    if check_order and isinstance(question, ChoiceQuestion) and len(labels) > 1:
        order = list(range(len(labels)))[::-1]
        raw2 = _evaluate(backend, question.reordered(order), state)
        if raw2.degraded:
            return DecisionAnswer.degraded_answer(question.type, raw2.backend, raw2.cause or "unknown")
        probs2, _, _ = _probabilities(raw2, temperature)
        best2 = order[max(range(len(probs2)), key=lambda i: probs2[i])]
        order_sensitive = best2 != argmax

    abstain = False
    reason: str | None = None
    if order_sensitive:
        abstain, reason = True, "order_sensitive: argmax changed under reversed option order"
    elif confidence < threshold:
        abstain, reason = True, f"confidence {confidence:.3f} < threshold {threshold:.3f}"

    answer = DecisionAnswer(
        question_type=question.type,
        backend=raw.backend,
        probabilities=prob_map,
        confidence=confidence,
        calibrated=calibrated,
        temperature=applied_t,
        abstain=abstain,
        abstain_reason=reason,
        order_sensitive=order_sensitive,
    )
    if isinstance(question, NoulQuestion):
        return _with(answer, noul=prob_map["true"])
    if isinstance(question, ChoiceQuestion):
        return _with(answer, choice=labels[argmax])
    if isinstance(question, ScoreQuestion):
        score = sum(i * p for i, p in enumerate(probs))
        legend = {str(i): level for i, level in enumerate(question.levels)}
        return _with(answer, score=score, legend=legend)
    raise TypeError(f"unsupported question {type(question).__name__}")


def _with(answer: DecisionAnswer, **changes: Any) -> DecisionAnswer:
    from dataclasses import replace

    return replace(answer, **changes)
