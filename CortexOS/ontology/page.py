"""Studio ontology page: the pack vocabulary already stored as YAML.

A listing of object types, links, actions, functions, metrics, and the
graph those files describe. It is not the Insights where-and-importance
ranking (``ontology_body``) and it is not the constructor catalog.

Metric-to-object links are the table names written in each metric's SQL
plus the metric ids an object measure already declares. Nothing here
matches question text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from CortexOS.ontology.registry import (
    ActionType,
    FunctionDef,
    LinkType,
    ObjectType,
    default_pack,
    load_action_types,
    load_functions,
    load_link_types,
    load_object_types,
    pack_dir_for,
)

# Table names in metric SQL (FROM / JOIN). Not applied to questions.
_TABLE_REF = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)", re.IGNORECASE)
_PACK_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SECTIONS = frozenset(
    {"summary", "objects", "links", "actions", "functions", "metrics", "graph"}
)


class OntologyPageError(Exception):
    """A named refusal. ``code`` is safe to return; it never carries a path or SQL."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def ontology_page(section: str, pack: str | None = None) -> dict[str, Any]:
    """One Studio section for ``pack`` (default: the active pack)."""
    if section not in _SECTIONS:
        raise OntologyPageError("unknown_section")
    name = _checked_pack(pack)
    try:
        built = _build(name)
    except OntologyPageError:
        raise
    except Exception:
        raise OntologyPageError("ontology_unreadable") from None
    return built[section]


def _checked_pack(pack: str | None) -> str:
    raw = pack if pack is not None else default_pack()
    name = raw.strip() or default_pack().strip()
    if not _PACK_NAME.fullmatch(name):
        raise OntologyPageError("unknown_pack")
    root = pack_dir_for(name)
    if not (root / "ontology" / "object_types.yaml").is_file():
        raise OntologyPageError("unknown_pack")
    return name


def _build(pack: str) -> dict[str, dict[str, Any]]:
    pack_dir = pack_dir_for(pack)
    objects = load_object_types(pack_dir)
    links = load_link_types(pack_dir)
    actions = load_action_types(pack_dir)
    functions = load_functions(pack_dir)
    id_by_lower = {obj.id.lower(): obj.id for obj in objects}
    metrics = _load_metrics(pack_dir, id_by_lower)
    metric_ids = _metric_ids(objects, metrics)

    object_rows = [_object_row(obj, metric_ids[obj.id]) for obj in objects]
    link_rows = [_link_row(link) for link in links]
    action_rows = [_action_row(action) for action in actions]
    function_rows = [_function_row(fn) for fn in functions]
    degree = _degree(objects, links)
    node_rows = [
        _node_row(obj, metric_ids[obj.id], degree[obj.id]) for obj in objects
    ]
    edge_rows = [_edge_row(link) for link in links]
    missing = [obj.id for obj in objects if not metric_ids[obj.id]]

    counts = {
        "object_types": len(object_rows),
        "properties": sum(row["property_count"] for row in object_rows),
        "sensitive_properties": sum(row["sensitive_count"] for row in object_rows),
        "link_types": len(link_rows),
        "action_types": len(action_rows),
        "action_tools": sum(1 for row in action_rows if row["kind"] == "tool"),
        "action_events": sum(1 for row in action_rows if row["kind"] == "event"),
        "functions": len(function_rows),
        "metrics": len(metrics),
    }
    summary = {
        "ok": True,
        "pack": pack,
        "counts": counts,
        "objects_without_metrics": missing,
    }
    return {
        "summary": summary,
        "objects": {"ok": True, "pack": pack, "object_types": object_rows},
        "links": {"ok": True, "pack": pack, "link_types": link_rows},
        "actions": {"ok": True, "pack": pack, "action_types": action_rows},
        "functions": {"ok": True, "pack": pack, "functions": function_rows},
        "metrics": {"ok": True, "pack": pack, "metrics": metrics},
        "graph": {"ok": True, "pack": pack, "nodes": node_rows, "edges": edge_rows},
    }


def _object_row(obj: ObjectType, metric_ids: list[str]) -> dict[str, Any]:
    properties = [
        {"name": prop.name, "type": prop.type, "agent_visible": prop.agent_visible}
        for prop in obj.properties
    ]
    return {
        "id": obj.id,
        "description": obj.description,
        "primary_key": obj.primary_key,
        "properties": properties,
        "property_count": len(properties),
        "sensitive_count": sum(1 for prop in properties if not prop["agent_visible"]),
        "metric_ids": list(metric_ids),
    }


def _link_row(link: LinkType) -> dict[str, Any]:
    return {
        "id": link.id,
        "from_object": link.from_object,
        "from_property": link.from_property,
        "to_object": link.to_object,
        "to_property": link.to_property,
        "cardinality": link.cardinality,
    }


def _action_row(action: ActionType) -> dict[str, Any]:
    return {
        "id": action.id,
        "kind": action.kind,
        "description": action.description,
        "ledger_event_type": action.ledger_event_type,
        "object_type": action.object_type,
        "required_role": action.required_role,
        "requires_confirm": action.requires_confirm,
        "params": list(action.params),
    }


def _function_row(fn: FunctionDef) -> dict[str, Any]:
    return {
        "id": fn.id,
        "description": fn.description,
        "module": fn.module,
        "callable": fn.callable,
    }


def _node_row(obj: ObjectType, metric_ids: list[str], degree: int) -> dict[str, Any]:
    sensitive = sum(1 for prop in obj.properties if not prop.agent_visible)
    return {
        "id": obj.id,
        "label": obj.id,
        "description": obj.description,
        "primary_key": obj.primary_key,
        "property_count": len(obj.properties),
        "sensitive_count": sensitive,
        "metric_count": len(metric_ids),
        "degree": degree,
    }


def _edge_row(link: LinkType) -> dict[str, Any]:
    return {
        "id": link.id,
        "source": link.from_object,
        "target": link.to_object,
        "source_property": link.from_property,
        "target_property": link.to_property,
        "cardinality": link.cardinality,
    }


def _degree(objects: list[ObjectType], links: list[LinkType]) -> dict[str, int]:
    degree = {obj.id: 0 for obj in objects}
    for link in links:
        if link.from_object in degree:
            degree[link.from_object] += 1
        if link.to_object in degree:
            degree[link.to_object] += 1
    return degree


def _metric_ids(objects: list[ObjectType], metrics: list[dict[str, Any]]) -> dict[str, list[str]]:
    by_object: dict[str, list[str]] = {obj.id: [] for obj in objects}
    for metric in metrics:
        for obj_id in metric["object_types"]:
            bucket = by_object.get(obj_id)
            if bucket is not None and metric["id"] not in bucket:
                bucket.append(metric["id"])
    for obj in objects:
        bucket = by_object[obj.id]
        for measure in obj.measures:
            for metric_id in measure.metrics:
                if metric_id not in bucket:
                    bucket.append(metric_id)
    return by_object


def _load_metrics(pack_dir: Path, id_by_lower: dict[str, str]) -> list[dict[str, Any]]:
    path = pack_dir / "semantic" / "metrics.yaml"
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise OntologyPageError("ontology_unreadable")
    rows: list[dict[str, Any]] = []
    for raw in data.get("metrics") or []:
        if not isinstance(raw, dict):
            continue
        metric_id = raw.get("id")
        if not isinstance(metric_id, str) or not metric_id:
            continue
        sql = raw.get("sql")
        sql_text = sql.strip() if isinstance(sql, str) else ""
        synonyms = raw.get("synonyms") if isinstance(raw.get("synonyms"), list) else []
        columns = raw.get("result_columns") if isinstance(raw.get("result_columns"), list) else []
        params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
        rows.append(
            {
                "id": metric_id,
                "kind": str(raw.get("kind") or ""),
                "synonyms": [str(item) for item in synonyms if isinstance(item, str)],
                "result_columns": [str(item) for item in columns if isinstance(item, str)],
                "params": [str(name) for name in params],
                "object_types": _object_types_in_sql(sql_text, id_by_lower),
                "sql": sql_text,
            }
        )
    return rows


def _object_types_in_sql(sql: str, id_by_lower: dict[str, str]) -> list[str]:
    found: list[str] = []
    for match in _TABLE_REF.finditer(sql):
        canonical = id_by_lower.get(match.group(1).lower())
        if canonical and canonical not in found:
            found.append(canonical)
    return found
