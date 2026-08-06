"""META-01 — prose catalog of what the semantic layer can answer.

No SQL, no invented numbers: lists certified questions, governed metrics,
and table names from ``load_all()`` for browse / metadata intent.
"""
from __future__ import annotations

import re

_CATALOG_INTENT = re.compile(
    r"\b(?:meta\s*data|metadata)\b|"
    r"\bwhat\s+(?:can|could)\s+(?:i|we)\s+(?:ask|search(?:\s+for)?|query|look\s+(?:up|for))\b|"
    r"\bwhat\s+else\b.*\b(?:ask|search|query|meta\s*data|metadata)\b|"
    r"\bwhat\s+(?:columns?|tables?|metrics?)\s+(?:are\s+)?(?:available|there|in\s+(?:the\s+)?data)\b|"
    r"\bwhat\s+is\s+available\s+in\s+(?:the\s+)?data\b|"
    r"\bbrowse\s+(?:the\s+)?(?:ontology|catalog)\b|"
    r"\b(?:list|show)\s+(?:all\s+)?(?:available\s+)?(?:metrics?|tables?|columns?)\b",
    re.I,
)


def is_catalog_intent(question: str) -> bool:
    """True when the user is asking what they can query, not asking for data."""
    return bool(_CATALOG_INTENT.search(question or ""))


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
