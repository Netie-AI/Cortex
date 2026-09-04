"""Read-only ontology payload for DMS GET /v1/ontology (via Cortex /dms/ontology).

YAML registry + semantic metrics. No invented rows. Viewer HTTP is routes.py.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from packs.dms.ontology.registry import (
    PACK_DIR,
    load_action_types,
    load_functions,
    load_link_types,
    load_object_types,
)
from packs.dms.semantic.loader import load_all


def pack_dir_for_name(pack: str | None) -> Path:
    name = (pack or "dms").strip() or "dms"
    if name == "dms":
        return PACK_DIR
    return PACK_DIR.parent / name


def _metric_ids_by_object(object_ids: set[str]) -> dict[str, list[str]]:
    by_obj: dict[str, list[str]] = defaultdict(list)
    for metric in load_all().metrics.values():
        for table in metric.tables:
            if table in object_ids and metric.id not in by_obj[table]:
                by_obj[table].append(metric.id)
    return dict(by_obj)


def _object_rows(pack_dir: Path) -> list[dict[str, Any]]:
    objects = load_object_types(pack_dir)
    metric_ids = _metric_ids_by_object({o.id for o in objects})
    rows: list[dict[str, Any]] = []
    for o in objects:
        ids = metric_ids.get(o.id, [])
        rows.append(
            {
                "id": o.id,
                "description": o.description,
                "primary_key": o.primary_key,
                "properties": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "agent_visible": p.agent_visible,
                    }
                    for p in o.properties
                ],
                "property_count": len(o.properties),
                "sensitive_count": sum(1 for p in o.properties if not p.agent_visible),
                "metric_ids": ids,
            }
        )
    return rows


def _link_rows(pack_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "id": link.id,
            "from_object": link.from_object,
            "from_property": link.from_property,
            "to_object": link.to_object,
            "to_property": link.to_property,
            "cardinality": link.cardinality,
        }
        for link in load_link_types(pack_dir)
    ]


def _action_rows(pack_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "id": a.id,
            "kind": a.kind,
            "description": a.description,
            "ledger_event_type": a.ledger_event_type,
            "object_type": a.object_type,
            "required_role": a.required_role,
            "requires_confirm": a.requires_confirm,
            "params": list(a.params),
        }
        for a in load_action_types(pack_dir)
    ]


def _function_rows(pack_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "id": fn.id,
            "description": fn.description,
            "module": fn.module,
            "callable": fn.callable,
        }
        for fn in load_functions(pack_dir)
    ]


def _metric_rows(object_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric in load_all().metrics.values():
        rows.append(
            {
                "id": metric.id,
                "kind": metric.kind,
                "synonyms": list(metric.synonyms),
                "result_columns": list(metric.result_columns),
                "params": list(metric.params.keys()),
                "object_types": [t for t in metric.tables if t in object_ids],
                "sql": metric.sql,
            }
        )
    return rows


def _counts(
    objects: list[dict[str, Any]],
    links: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    functions: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
) -> dict[str, int]:
    props = sum(o["property_count"] for o in objects)
    sensitive = sum(o["sensitive_count"] for o in objects)
    tools = sum(1 for a in actions if a["kind"] == "tool")
    events = sum(1 for a in actions if a["kind"] != "tool")
    return {
        "object_types": len(objects),
        "properties": props,
        "sensitive_properties": sensitive,
        "link_types": len(links),
        "action_types": len(actions),
        "action_tools": tools,
        "action_events": events,
        "functions": len(functions),
        "metrics": len(metrics),
    }


def _graph(
    objects: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    degree: dict[str, int] = defaultdict(int)
    for link in links:
        degree[link["from_object"]] += 1
        degree[link["to_object"]] += 1
    nodes = [
        {
            "id": o["id"],
            "label": o["id"],
            "description": o["description"],
            "primary_key": o["primary_key"],
            "property_count": o["property_count"],
            "sensitive_count": o["sensitive_count"],
            "metric_count": len(o["metric_ids"]),
            "degree": degree.get(o["id"], 0),
        }
        for o in objects
    ]
    edges = [
        {
            "id": link["id"],
            "source": link["from_object"],
            "target": link["to_object"],
            "source_property": link["from_property"],
            "target_property": link["to_property"],
            "cardinality": link["cardinality"],
        }
        for link in links
    ]
    return {"nodes": nodes, "edges": edges}


def ontology_bundle(pack: str | None = None) -> dict[str, Any]:
    pack_dir = pack_dir_for_name(pack)
    objects = _object_rows(pack_dir)
    links = _link_rows(pack_dir)
    actions = _action_rows(pack_dir)
    functions = _function_rows(pack_dir)
    object_ids = {o["id"] for o in objects}
    metrics = _metric_rows(object_ids)
    missing = [o["id"] for o in objects if not o["metric_ids"]]
    name = pack_dir.name
    return {
        "pack": name,
        "counts": _counts(objects, links, actions, functions, metrics),
        "objects_without_metrics": missing,
        "object_types": objects,
        "link_types": links,
        "action_types": actions,
        "functions": functions,
        "metrics": metrics,
        **_graph(objects, links),
    }


def ontology_section(section: str, pack: str | None = None) -> dict[str, Any]:
    bundle = ontology_bundle(pack)
    if section in ("", "summary"):
        return {
            "pack": bundle["pack"],
            "counts": bundle["counts"],
            "objects_without_metrics": bundle["objects_without_metrics"],
        }
    if section == "objects":
        return {"object_types": bundle["object_types"]}
    if section == "links":
        return {"link_types": bundle["link_types"]}
    if section == "actions":
        return {"action_types": bundle["action_types"]}
    if section == "functions":
        return {"functions": bundle["functions"]}
    if section == "metrics":
        return {"metrics": bundle["metrics"]}
    if section == "graph":
        return {"nodes": bundle["nodes"], "edges": bundle["edges"]}
    raise KeyError(section)
