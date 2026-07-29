"""Decide whether a chat message should become a background workflow, and which.

Deterministic first. Scoring is boring on purpose: a model call to answer "is
this a research request?" costs a round-trip on every message typed, and gets it
wrong in ways nobody can debug. Trigger phrases are cheap, inspectable, and a
user can see why their message did or did not fan out.

The model is consulted only when the deterministic pass is *ambiguous* — two
templates within a hair of each other — and even then a failure to reach it
falls back to the top deterministic pick rather than blocking the message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from CortexOS.execution import workflow_templates

#: Phrasings that mean "do this as a background fan-out" regardless of topic.
_EXPLICIT_FANOUT = (
    "invoke subagent",
    "invoke subagents",
    "spin off",
    "spin up agents",
    "spawn agents",
    "spawn subagent",
    "in the background",
    "background task",
    "run a workflow",
    "use a workflow",
    "fan out",
    "deep dive",
)

#: Phrasings that mean the opposite — a quick answer, not a fleet.
_SUPPRESS = (
    "quick question",
    "just tell me",
    "one line",
    "briefly",
    "tl;dr",
    "short answer",
    "don't research",
    "no need to search",
    "single agent",
    "don't fan out",
    "no subagents",
    "stay simple",
)

_TOPIC_AFTER = re.compile(
    r"\b(?:research|investigate|look into|dig into|find out about|study|survey)\b[:\s]+(.{3,200})",
    re.IGNORECASE,
)
_TARGET_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/][\w.\-\\/]+|\b[\w\-]+\.(?:py|ts|tsx|js|jsx|html|css|md|json|xaml|cs)\b")
_WORD = re.compile(r"[a-z0-9']+")

MIN_SCORE = 2.0
AMBIGUOUS_MARGIN = 1.0


@dataclass(frozen=True, slots=True)
class Recognition:
    template_id: str | None
    confidence: float
    why: str
    variables: dict[str, Any]
    explicit: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "confidence": round(self.confidence, 3),
            "why": self.why,
            "variables": dict(self.variables),
            "explicit": self.explicit,
            "should_run": self.template_id is not None,
        }


def _score(text: str, template: workflow_templates.WorkflowTemplate) -> tuple[float, list[str]]:
    """Longer trigger phrases score higher — "performance audit" is far more
    telling than "audit", and rewarding specificity keeps generic verbs from
    dominating."""
    hits: list[str] = []
    score = 0.0
    for trigger in template.triggers:
        if trigger in text:
            weight = 1.0 + 0.5 * trigger.count(" ")
            if re.search(rf"\b{re.escape(trigger)}\b", text):
                weight += 0.5
            score += weight
            hits.append(trigger)
    return score, hits


def _extract_target(text: str, original: str) -> str:
    paths = _TARGET_PATH.findall(original)
    if paths:
        return paths[0]
    match = _TOPIC_AFTER.search(original)
    if match:
        return match.group(1).strip().rstrip(".?!")
    return ""


def _extract_topic(original: str) -> str:
    match = _TOPIC_AFTER.search(original)
    if match:
        return match.group(1).strip().rstrip(".?!")
    # Fall back to the message itself, trimmed — the planning agent can cope
    # with a whole sentence, and guessing a shorter topic loses qualifiers.
    return original.strip()[:400]


def recognize(
    prompt: str,
    *,
    context: Mapping[str, Any] | None = None,
) -> Recognition:
    """Score every template against the message and pick a winner, or none."""
    original = (prompt or "").strip()
    text = original.lower()
    if not text:
        return Recognition(None, 0.0, "empty message", {}, False)

    if any(s in text for s in _SUPPRESS):
        return Recognition(None, 0.0, "message asks for a short direct answer", {}, False)

    explicit = any(p in text for p in _EXPLICIT_FANOUT)

    scored: list[tuple[float, workflow_templates.WorkflowTemplate, list[str]]] = []
    for template in workflow_templates.TEMPLATES:
        score, hits = _score(text, template)
        if explicit:
            score += 1.5
        scored.append((score, template, hits))
    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best, best_hits = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0

    threshold = MIN_SCORE if not explicit else 1.0
    if best_score < threshold:
        if explicit:
            # "invoke a subagent to help me with X" with no domain signal is
            # still a fan-out request — research is the general-purpose answer.
            best = workflow_templates.DEEP_RESEARCH
            best_hits = ["explicit fan-out request"]
            best_score = threshold
        else:
            # Report a normalized, clamped confidence — never the raw score.
            # A near-miss ("research X" alone) still reads as weak signal, but
            # must stay well under the chat auto-fire gate so it does not fan
            # out an expensive workflow on one keyword.
            return Recognition(
                None,
                round(min(0.4, best_score / 6.0), 3),
                "no template scored above the threshold",
                {},
                False,
            )

    ctx = dict(context or {})
    variables: dict[str, Any] = {
        "topic": _extract_topic(original),
        "target": _extract_target(text, original) or str(ctx.get("target") or ctx.get("workspace") or ""),
        "prompt": original,
    }
    for key in best.inputs:
        if not variables.get(key):
            variables[key] = variables.get("topic") or original[:400]

    margin = best_score - runner_up
    confidence = min(1.0, best_score / 6.0) * (0.7 if margin < AMBIGUOUS_MARGIN else 1.0)
    why = (
        f"matched {best.name} on {', '.join(best_hits[:4])}"
        if best_hits
        else f"defaulted to {best.name}"
    )
    if margin < AMBIGUOUS_MARGIN and len(scored) > 1 and scored[1][0] >= threshold:
        why += f" (close call against {scored[1][1].name})"

    return Recognition(best.id, confidence, why, variables, explicit)


def describe() -> list[dict[str, Any]]:
    """What the recognizer keys on — the UI shows this so the behaviour is not a
    black box the user has to reverse-engineer by trial."""
    return [
        {
            "template_id": t.id,
            "name": t.name,
            "triggers": list(t.triggers),
        }
        for t in workflow_templates.TEMPLATES
    ] + [
        {"template_id": "*", "name": "Explicit fan-out", "triggers": list(_EXPLICIT_FANOUT)},
        {"template_id": None, "name": "Suppressed", "triggers": list(_SUPPRESS)},
    ]
