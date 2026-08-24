"""META-01 — prose catalog of what the semantic layer can answer.

No SQL, no invented numbers: lists certified questions, governed metrics,
and table names from ``load_all()`` for browse / metadata intent.
"""
from __future__ import annotations

import re

# Intent classes, not a phrase list. A catalog ask is "what exists / what can
# I query", not a request for a specific measure. Adding a new wording in the
# same class should not require a new literal.
_CAPABILITY = re.compile(
    r"\b(?:what|which)\s+(?:else\s+)?"
    r"(?:can|could)\s+(?:i|we)\s+"
    r"(?:ask|search(?:\s+for)?|query|look\s+(?:up|for)|browse)\b",
    re.I,
)
_INVENTORY = re.compile(
    r"\b(?:what|which)\s+(?:else\s+)?"
    r"(?:data|tables?|columns?|metrics?|questions?|meta\s*data|metadata)\s+"
    r"(?:do\s+you\s+have|are\s+(?:available|there)|is\s+available|"
    r"can\s+(?:i|we)\s+(?:search|query|ask|browse)|in\s+(?:the\s+)?data)\b",
    re.I,
)
_AVAILABLE_IN_DATA = re.compile(
    r"\bwhat\s+(?:else\s+)?is\s+available\s+in\s+(?:the\s+)?data\b",
    re.I,
)
_BROWSE = re.compile(
    r"\b(?:show|list|browse|open)\s+(?:me\s+)?(?:the\s+)?(?:available\s+)?"
    r"(?:catalog|ontology|schema|meta\s*data|metadata|metrics?|tables?|columns?)\b",
    re.I,
)
_METADATA = re.compile(r"\b(?:meta\s*data|metadata)\b", re.I)


def is_catalog_intent(question: str) -> bool:
    """True when the user is asking what they can query, not asking for data."""
    text = question or ""
    return bool(
        _CAPABILITY.search(text)
        or _INVENTORY.search(text)
        or _AVAILABLE_IN_DATA.search(text)
        or _BROWSE.search(text)
        or _METADATA.search(text)
    )


def _metric_label(metric) -> str:
    if metric.synonyms:
        return str(metric.synonyms[0])
    return metric.id.replace("_", " ")


def build_catalog_answer(
    *,
    certified_cap: int = 8,
    metrics_cap: int = 8,
) -> dict[str, str]:
    """Prose summary of certified questions, metrics, and tables."""
    from packs.dms.semantic.loader import load_all

    model = load_all()
    certified = [cq.question for cq in model.certified[:certified_cap]]
    metrics: list[str] = []
    for metric in model.metrics.values():
        metrics.append(_metric_label(metric))
        if len(metrics) >= metrics_cap:
            break
    tables = list((model.base.get("tables") or {}).keys())

    lines = [
        "Here is what you can ask from the DMS semantic layer:",
        "",
        "**Certified questions** (exact wording gets a verified answer):",
        *[f"- {q}" for q in certified],
        "",
        "**Governed metrics** (paraphrases compile to verified SQL):",
        *[f"- {m}" for m in metrics],
        "",
        f"**Tables covered:** {', '.join(tables)}.",
        "",
        "Pick one above, or rephrase using those topics.",
    ]
    return {
        "answer": "\n".join(lines),
        "layer": "catalog",
        "badge": "catalog",
    }
