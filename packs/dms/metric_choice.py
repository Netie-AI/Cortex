"""OpenVault implementation of the engine's metric-choice port.

The pack registers this. The engine never imports the pack to reach it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from CortexOS.dms.metric_choice import (
    CHECK_INSTRUCTIONS,
    CHECK_TASK,
    CHOOSE_INSTRUCTIONS,
    CHOOSE_TASK,
    UNARMED_CLOSEST,
    MetricSelection,
    MetricVerdict,
)


def ontology_subjects() -> str:
    """Subjects the semantic layer defines, for the model. Not a serve gate."""
    from CortexOS.dms.warehouse_db import load_semantic_layer

    base = load_semantic_layer()
    lines = ["Tables:"]
    tables = base.get("tables") or {}
    for name in tables:
        lines.append(f"- {name}")
    lines.append("Glossary:")
    for item in base.get("glossary") or []:
        if isinstance(item, dict) and item.get("term"):
            lines.append(f"- {item['term']}")
    return "\n".join(lines)


def metric_catalog() -> str:
    from packs.dms.semantic.loader import load_all

    lines: list[str] = []
    for metric in load_all().metrics.values():
        params = ", ".join(sorted(metric.params)) or "-"
        synonyms = "; ".join(metric.synonyms or [])
        sql = " ".join(metric.sql.split())
        if len(sql) > 240:
            sql = sql[:240] + "..."
        lines.append(
            f"- {metric.id} | params: {params} | wordings: {synonyms} | sql: {sql}"
        )
    return "\n".join(lines)


def _json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        body = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _known_slots(metric_id: str, slots: Any) -> dict[str, Any]:
    from packs.dms.semantic.loader import SemanticError, load_all

    if metric_id == "none" or not isinstance(slots, dict):
        return {}
    try:
        spec = load_all().metric(metric_id).params
    except SemanticError:
        return {}
    clean: dict[str, Any] = {}
    for key, value in slots.items():
        if key not in spec:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[str(key)] = value
    return clean


# A resolution at or above this is an exact, normalised or declared-encoding
# match (values.resolve steps 1-3). Containment and fuzzy matches score below.
_EXACT_VALUE = 0.95


def _inexact_values(metric_id: str, slots: dict[str, Any]) -> list[str]:
    """Slot values the model wrote that are not a stored value of their column.

    Containment and fuzzy matching would map ``NOT_DELAYED`` or ``UNDELAYED``
    back to ``DELAYED``, so a model's slot counts only when it names a stored
    value exactly (case, spacing and declared encodings aside).
    """
    from packs.dms.semantic import values as valuedict
    from packs.dms.semantic.loader import load_all

    spec = load_all().metric(metric_id).params
    loose: list[str] = []
    for key, raw in slots.items():
        param = spec.get(key) or {}
        column = param.get("column")
        if param.get("kind") not in ("value", "filter", "not_in") or not column:
            continue
        items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        for item in items:
            text = "" if item is None else str(item).strip()
            if not text:
                continue
            res = valuedict.resolve(text, column)
            if res.value is None or res.confidence < _EXACT_VALUE:
                loose.append(f"{key}={text}")
    return loose


def _names_a_metric_id(text: str) -> bool:
    """Whether customer text shows a declared metric id. Ids are internal."""
    from packs.dms.semantic.loader import load_all

    folded = text.casefold()
    return any(mid.casefold() in folded for mid in load_all().metrics)


def _customer_text(raw: Any) -> str:
    """One line of model text for the customer, or empty when it shows an id."""
    text = " ".join(str(raw or "").split())
    return "" if not text or _names_a_metric_id(text) else text


def _chosen_why(metric_id: str) -> str:
    return f"the metric chooser chose the declared metric {metric_id}"


def _agreed_why(metric_id: str) -> str:
    return f"{_chosen_why(metric_id)} and the meaning check agreed"


class OpenVaultMetricChoice:
    """FreeRoute is the only model path. Unarmed or unreadable output is none."""

    def choose(self, question: str) -> MetricSelection:
        from CortexOS.integrations import freeroute

        if not freeroute.arming().armed:
            return MetricSelection(
                closest_question=UNARMED_CLOSEST,
                why="the metric chooser is not armed, so it cannot be served directly",
            )
        out = freeroute.complete(
            CHOOSE_TASK,
            [
                {"role": "system", "content": CHOOSE_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\n"
                        f"Subjects:\n{ontology_subjects()}\n\n"
                        f"Metrics:\n{metric_catalog()}"
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=400,
        )
        if not out.ok:
            return MetricSelection(
                closest_question=UNARMED_CLOSEST,
                why="the metric chooser is not armed, so it cannot be served directly",
            )
        body = _json_object(out.text)
        if body is None:
            return MetricSelection(
                closest_question=UNARMED_CLOSEST,
                why="the metric chooser returned nothing usable, so it cannot be served directly",
            )
        metric_id = str(body.get("metric_id") or "none").strip()
        if metric_id.lower() == "none":
            metric_id = "none"
        else:
            from packs.dms.semantic.loader import SemanticError, load_all

            try:
                load_all().metric(metric_id)
            except SemanticError:
                metric_id = "none"
        # The engine asks this back before it is offered (answerable_closest).
        closest = _customer_text(body.get("closest_question")) or UNARMED_CLOSEST
        slots = _known_slots(metric_id, body.get("slots"))
        loose = _inexact_values(metric_id, slots) if metric_id != "none" else []
        if loose:
            # The slot names are internal; the reconfirm does not show them.
            return MetricSelection(
                closest_question=closest,
                why=(
                    "the question filters on a value the data does not store, "
                    "so it cannot be served directly"
                ),
            )
        if metric_id == "none":
            stated = _customer_text(body.get("why"))
            why = (
                f"{stated.rstrip('.')}, so it cannot be served directly"
                if stated
                else "the metric chooser did not accept a declared metric"
            )
        else:
            # A served answer carries this why as its assumption. It states
            # what was chosen. Refusal prose belongs only on none.
            why = _chosen_why(metric_id)
        return MetricSelection(
            metric_id=metric_id,
            slots=slots,
            closest_question=closest,
            why=why,
        )

    def check(self, question: str, selection: MetricSelection) -> MetricVerdict:
        from CortexOS.integrations import freeroute
        from packs.dms.semantic.loader import SemanticError, load_all

        if not freeroute.arming().armed:
            return MetricVerdict(
                agrees=False,
                unsure=True,
                why="the metric check is not armed, so it cannot be served directly",
            )
        try:
            metric = load_all().metric(selection.metric_id)
        except SemanticError:
            return MetricVerdict(agrees=False, unsure=True, why="unknown metric")
        described = (
            f"metric_id: {metric.id}\n"
            f"wordings: {'; '.join(metric.synonyms or [])}\n"
            f"slots: {selection.slots}\n"
            f"sql: {' '.join(metric.sql.split())}"
        )
        out = freeroute.complete(
            CHECK_TASK,
            [
                {"role": "system", "content": CHECK_INSTRUCTIONS},
                {"role": "user", "content": f"Question: {question}\n\n{described}"},
            ],
            temperature=0.0,
            max_tokens=200,
        )
        if not out.ok:
            return MetricVerdict(
                agrees=False,
                unsure=True,
                why="the metric check is not armed, so it cannot be served directly",
            )
        body = _json_object(out.text)
        if body is None:
            return MetricVerdict(
                agrees=False,
                unsure=True,
                why="the metric check returned nothing usable",
            )
        # Only the JSON booleans agrees=true and unsure=false agree. A string
        # ("false", "no", "true"), a number, null or a missing key is not an
        # agreement: truthiness would read "false" as yes and serve the
        # positive metric for its complement (#412).
        raw_agrees = body.get("agrees")
        raw_unsure = body.get("unsure")
        if raw_agrees is True and raw_unsure is False:
            selection.why = _agreed_why(selection.metric_id)
            return MetricVerdict(agrees=True, unsure=False, why="")
        if raw_agrees is False and raw_unsure is False:
            return MetricVerdict(agrees=False, unsure=False, why="")
        if raw_unsure is True:
            return MetricVerdict(agrees=False, unsure=True, why="")
        return MetricVerdict(
            agrees=False,
            unsure=True,
            why=(
                "the metric check did not answer with a plain yes or no, "
                "so it cannot be served directly"
            ),
        )
