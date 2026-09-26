"""Question and answer models for the calibrated decision port.

Shapes follow the kev ``POST /v1/systemone`` wire format (noul / choice / score)
so a local kev server can be one backend among others. Nothing here imports
``packs.*`` or any network library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_OPTIONS = 255
NOUL_LABELS: tuple[str, str] = ("false", "true")


@dataclass(frozen=True, slots=True)
class NoulQuestion:
    """Yes/no question. ``P(true)`` is the answer."""

    instructions: str
    true_description: str | None = None
    false_description: str | None = None

    @property
    def type(self) -> str:
        return "noul"

    @property
    def labels(self) -> tuple[str, ...]:
        return NOUL_LABELS

    def to_kev(self) -> dict[str, Any]:
        body: dict[str, Any] = {"type": "noul", "instructions": self.instructions}
        criteria: dict[str, str] = {}
        if self.true_description is not None:
            criteria["true"] = self.true_description
        if self.false_description is not None:
            criteria["false"] = self.false_description
        if criteria:
            body["criteria"] = criteria
        return body


@dataclass(frozen=True, slots=True)
class ChoiceQuestion:
    """Pick one of 1..255 named options. Option order must not matter."""

    instructions: str
    options: tuple[tuple[str, str], ...]  # (name, description), in presentation order

    def __post_init__(self) -> None:
        _validate_names([name for name, _ in self.options], "choice options")

    @property
    def type(self) -> str:
        return "choice"

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.options)

    def reordered(self, order: list[int]) -> ChoiceQuestion:
        return ChoiceQuestion(self.instructions, tuple(self.options[i] for i in order))

    def to_kev(self) -> dict[str, Any]:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": {name: desc for name, desc in self.options},
        }


@dataclass(frozen=True, slots=True)
class ScoreQuestion:
    """Ordered levels, 1..255. The answer is the probability-weighted level index."""

    instructions: str
    levels: tuple[str, ...]  # ordered level descriptions

    def __post_init__(self) -> None:
        _validate_names(list(self.levels), "score levels")

    @property
    def type(self) -> str:
        return "score"

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(str(i) for i in range(len(self.levels)))

    def to_kev(self) -> dict[str, Any]:
        return {"type": "score", "instructions": self.instructions, "criteria": list(self.levels)}


Question = NoulQuestion | ChoiceQuestion | ScoreQuestion


def _validate_names(names: list[str], what: str) -> None:
    if not 1 <= len(names) <= MAX_OPTIONS:
        raise ValueError(f"{what}: expected 1..{MAX_OPTIONS} entries, got {len(names)}")
    if any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError(f"{what}: names must be non-empty strings")
    if len(set(names)) != len(names):
        raise ValueError(f"{what}: names must be unique")


@dataclass(frozen=True, slots=True)
class RawDecision:
    """What a backend returns before calibration.

    ``scores`` are aligned with ``question.labels``. When ``is_logits`` is true a
    temperature can be applied; otherwise they are already probabilities.
    ``calibrated`` says whether the backend fitted a temperature itself.
    ``degraded`` with a ``cause`` means the backend could not answer.
    """

    scores: tuple[float, ...]
    is_logits: bool
    calibrated: bool
    backend: str
    degraded: bool = False
    cause: str | None = None

    @staticmethod
    def failure(backend: str, cause: str) -> RawDecision:
        return RawDecision(
            scores=(), is_logits=False, calibrated=False, backend=backend, degraded=True, cause=cause
        )


@dataclass(frozen=True, slots=True)
class DecisionAnswer:
    """The calibrated, thresholded answer that callers act on."""

    question_type: str
    backend: str
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    calibrated: bool = False
    temperature: float | None = None
    abstain: bool = True
    abstain_reason: str | None = None
    order_sensitive: bool = False
    degraded: bool = False
    cause: str | None = None
    # per-type payloads
    noul: float | None = None
    choice: str | None = None
    score: float | None = None
    legend: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def degraded_answer(question_type: str, backend: str, cause: str) -> DecisionAnswer:
        return DecisionAnswer(
            question_type=question_type,
            backend=backend,
            abstain=True,
            abstain_reason=f"degraded: {cause}",
            degraded=True,
            cause=cause,
        )
