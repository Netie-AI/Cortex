"""Choose a governed metric with a model, then check that it means the question.

The keyword cascade and synonym equality do not decide what is served. The
port reads the question and the metric definitions and returns one declared
metric plus its filters, or none. A second call checks that choice.
Disagreement or uncertainty does not serve. No registered port, and an
unarmed port, fail closed: no metric.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

CHOOSE_TASK = "dms-metric-choose"
CHECK_TASK = "dms-metric-check"
UNARMED_CLOSEST = "a declared question"

CHOOSE_INSTRUCTIONS = (
    "Choose the one governed metric that means the same thing as the question. "
    "Use only the metric definitions. Return JSON and nothing else, with keys "
    "metric_id, slots, and closest_question. metric_id is a defined id, or "
    "none when no definition means the question. slots holds only filters the "
    "definition declares. closest_question is the declared wording of the "
    "nearest metric, or empty when there is none. Negation and complements "
    "are different questions from the positive metric: a question that says "
    "the opposite, an exception, or an absence is not that metric."
)

CHECK_INSTRUCTIONS = (
    "Does this governed metric mean the same thing as the question? "
    "Return JSON and nothing else, with keys agrees and unsure, both booleans. "
    "agrees is true only when the metric, including its filters, is the "
    "question. Negation and complements do not agree with the positive metric. "
    "unsure is true when you cannot tell. If unsure, agrees is false."
)


@dataclass(slots=True)
class MetricSelection:
    metric_id: str = "none"
    slots: dict[str, Any] = field(default_factory=dict)
    closest_question: str = ""
    why: str = ""
    # A rejected metric is not a document question. none still may be.
    allow_documents: bool = True


@dataclass(slots=True)
class MetricVerdict:
    agrees: bool = False
    unsure: bool = True
    why: str = ""


@dataclass(slots=True)
class MetricDecision:
    serve: bool
    selection: MetricSelection
    why: str


class MetricChoicePort(Protocol):
    def choose(self, question: str) -> MetricSelection: ...

    def check(self, question: str, selection: MetricSelection) -> MetricVerdict: ...


class _UnarmedChoice:
    """No port registered. Nothing is served."""

    def choose(self, question: str) -> MetricSelection:
        del question
        return MetricSelection(
            closest_question=UNARMED_CLOSEST,
            why="the metric chooser is not armed, so it cannot be served directly",
        )

    def check(self, question: str, selection: MetricSelection) -> MetricVerdict:
        del question, selection
        return MetricVerdict(
            agrees=False,
            unsure=True,
            why="the metric check is not armed, so it cannot be served directly",
        )


_port: MetricChoicePort | None = None


def register_metric_choice(port: MetricChoicePort) -> None:
    """Install the port. A later call replaces it."""
    global _port
    _port = port


def register_default_metric_choice(port: MetricChoicePort) -> None:
    """The pack's port. A port already installed by a test stays."""
    global _port
    if _port is None:
        _port = port


def clear_metric_choice() -> None:
    global _port
    _port = None


def resolve_metric_choice() -> MetricChoicePort:
    return _port if _port is not None else _UnarmedChoice()


def metric_check_enabled() -> bool:
    """Tests replace this. Off, the chosen metric is served without the check."""
    return True


def decide_metric(question: str) -> MetricDecision:
    """The serve decision: a chosen metric the check accepts, or do not serve."""
    selection = resolve_metric_choice().choose(question)
    metric_id = (selection.metric_id or "none").strip()
    if metric_id.lower() == "none" or not metric_id:
        why = selection.why or (
            "no declared metric was chosen, so it cannot be served directly"
        )
        return MetricDecision(False, selection, why)
    if not metric_check_enabled():
        return MetricDecision(True, selection, "")
    verdict = resolve_metric_choice().check(question, selection)
    if verdict.agrees and not verdict.unsure:
        return MetricDecision(True, selection, "")
    why = verdict.why or (
        f"the metric check does not agree that {metric_id} matches the question, "
        "so it cannot be served directly"
    )
    selection.allow_documents = False
    return MetricDecision(False, selection, why)
