"""Choose a governed metric with a model, then check that it means the question.

The keyword cascade and synonym equality do not decide what is served. The
port reads the question and the metric definitions and returns one declared
metric plus its filters, or none. A second call checks that choice.
Disagreement or uncertainty does not serve. No registered port, and an
unarmed port, fail closed: no metric.

A question that is not served reconfirms with the closest answerable question.
The model's suggestion is asked back through the same choose and check, so Yes
runs a question that is served. A suggestion that is not, or none at all,
becomes ``NO_CLOSEST``: Yes and No then both answer not found.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

CHOOSE_TASK = "dms-metric-choose"
CHECK_TASK = "dms-metric-check"
# The closest question when none was confirmed answerable. It is Cortex's
# own text, so Yes on it is recognised and runs nothing (see
# ``is_no_closest``).
NO_CLOSEST = "No question close to this one can be confirmed as answerable."
UNARMED_CLOSEST = NO_CLOSEST

CHOOSE_INSTRUCTIONS = (
    "Choose the one governed metric that means the same thing as the question. "
    "Use only the metric definitions and the subjects the ontology lists. "
    "Return JSON and nothing else, with keys metric_id, slots, why and "
    "closest_question. metric_id is a defined id, or none when no definition "
    "means the question. slots holds only filters the definition declares. "
    "When metric_id is none, why is one plain sentence for the person who "
    "asked, saying what the declared metrics cannot express about the "
    "question. closest_question is one complete question in plain language, "
    "as a person would ask it, that the nearest metric answers exactly as it "
    "is defined, using only filters it declares. Do not copy or join the "
    "wordings list, do not leave a fragment, and never write a metric id or "
    "a column name in why or closest_question. closest_question is empty "
    "when no metric is close. A question about a subject that is not in the "
    "ontology list is none: do not substitute a nearby subject. A question no "
    "definition can express, including a total over a ranking or a grouping "
    "no definition returns, is none. Negation and complements are different "
    "questions from the positive metric: a question that says the opposite, "
    "an exception, or an absence is not that metric."
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


def is_no_closest(question: str) -> bool:
    """Whether ``question`` is the closest question that names nothing to run."""
    return " ".join(str(question or "").split()) == NO_CLOSEST


def _choose_and_check(question: str) -> MetricDecision:
    port = resolve_metric_choice()
    selection = port.choose(question)
    metric_id = (selection.metric_id or "none").strip()
    if metric_id.lower() == "none" or not metric_id:
        why = selection.why or (
            "no declared metric was chosen, so it cannot be served directly"
        )
        return MetricDecision(False, selection, why)
    if not metric_check_enabled():
        return MetricDecision(True, selection, "")
    verdict = port.check(question, selection)
    if verdict.agrees and not verdict.unsure:
        return MetricDecision(True, selection, "")
    # The id is internal. This text reaches the customer on a reconfirm.
    why = verdict.why or (
        "the closest declared metric does not mean the same thing as the question, "
        "so it cannot be served directly"
    )
    selection.allow_documents = False
    return MetricDecision(False, selection, why)


def answerable_closest(question: str, closest: str) -> str:
    """``closest`` when asking it serves a metric whose check agrees, else ``NO_CLOSEST``.

    Yes runs the closest question, so it is asked back through the same
    choose and check. A suggestion that would reconfirm again, that repeats
    the question, or that shows the id of the metric it maps to is not offered.
    """
    text = " ".join(str(closest or "").split())
    if not text or is_no_closest(text):
        return NO_CLOSEST
    if text.casefold() == " ".join(question.split()).casefold():
        return NO_CLOSEST
    probe = _choose_and_check(text)
    if not probe.serve:
        return NO_CLOSEST
    if probe.selection.metric_id.strip().casefold() in text.casefold():
        return NO_CLOSEST
    return text


def decide_metric(question: str) -> MetricDecision:
    """The serve decision: a chosen metric the check accepts, or do not serve.

    Not served, the selection carries the closest answerable question.
    """
    decision = _choose_and_check(question)
    if not decision.serve:
        selection = decision.selection
        selection.closest_question = answerable_closest(
            question, selection.closest_question
        )
    return decision
