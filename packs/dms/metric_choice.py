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
        closest = " ".join(str(body.get("closest_question") or "").split())
        if not closest:
            closest = UNARMED_CLOSEST
        return MetricSelection(
            metric_id=metric_id,
            slots=_known_slots(metric_id, body.get("slots")),
            closest_question=closest,
            why="the metric chooser did not accept a declared metric",
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
        unsure = bool(body.get("unsure"))
        agrees = bool(body.get("agrees")) and not unsure
        return MetricVerdict(agrees=agrees, unsure=unsure, why="")
