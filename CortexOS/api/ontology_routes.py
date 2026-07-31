"""Ontology read API — ``GET /dms/ontology/*`` over the pack ontology registry.

Engine-canonical and pack-agnostic: every payload comes from
``CortexOS.ontology.registry`` loaders, which read ``packs/<pack>/ontology/*.yaml``
**by path**. This module therefore holds no ``packs`` import — the F7 role gate is
*injected by the registrar* (``register_ontology_routes(app, dependencies=...)``),
so the routes stay behind RBAC without the engine reaching into a pack.

Read-only on purpose. The ontology is authored as YAML in the pack and compiled by
``registry.compile_to_sqlite``; an HTTP writer here would give the registry a second
source of truth that the YAML review path could not see.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

from CortexOS.ontology.registry import (
    default_pack,
    load_action_types,
    load_functions,
    load_link_types,
    load_object_types,
    pack_dir_for,
)

router = APIRouter(prefix="/dms/ontology", tags=["ontology"])


def _pack_dir(pack: str | None) -> Path:
    return pack_dir_for(pack)


def _load_metrics(pack: str | None) -> list[dict[str, Any]]:
    """Governed metrics for the pack — read by path, absent is not an error.

    The semantic layer is the answer plane's contract with the ontology: a metric
    names the objects it reads. Surfacing them together is the only way a steward
    can see that an object type has no governed way to be asked about.
    """
    path = _pack_dir(pack) / "semantic" / "metrics.yaml"
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    rows = data.get("metrics") if isinstance(data, dict) else data
    return [row for row in (rows or []) if isinstance(row, dict)]


def _objects_in_sql(sql: str, object_ids: list[str]) -> list[str]:
    return [oid for oid in object_ids if re.search(rf"\b{re.escape(oid)}\b", sql, re.IGNORECASE)]


def _metric_view(pack: str | None) -> list[dict[str, Any]]:
    objects = load_object_types(_pack_dir(pack))
    object_ids = [o.id for o in objects]
    out: list[dict[str, Any]] = []
    for m in _load_metrics(pack):
        sql = str(m.get("sql") or "")
        out.append(
            {
                "id": str(m.get("id") or ""),
                "kind": str(m.get("kind") or "unknown"),
                "synonyms": [str(s) for s in (m.get("synonyms") or [])],
                "result_columns": [str(c) for c in (m.get("result_columns") or [])],
                "params": sorted(str(k) for k in (m.get("params") or {})),
                "object_types": _objects_in_sql(sql, object_ids),
                "sql": sql.strip(),
            }
        )
    return out


def _object_view(pack: str | None) -> list[dict[str, Any]]:
    metrics = _metric_view(pack)
    out: list[dict[str, Any]] = []
    for obj in load_object_types(_pack_dir(pack)):
        touching = [m["id"] for m in metrics if obj.id in m["object_types"]]
        props = [asdict(p) for p in obj.properties]
        out.append(
            {
                "id": obj.id,
                "description": obj.description,
                "primary_key": obj.primary_key,
                "properties": props,
                "property_count": len(props),
                "sensitive_count": sum(1 for p in props if not p["agent_visible"]),
                "metric_ids": touching,
            }
        )
    return out


def _guarded(fn, *args: Any) -> Any:
    """Missing/broken pack YAML is a 404/422, never a 500 with a traceback."""
    try:
        return fn(*args)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"ontology not defined for pack: {exc}") from exc
    except (ValueError, KeyError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=f"ontology YAML invalid: {exc}") from exc


@router.get("")
def ontology_summary(pack: str | None = None) -> dict[str, Any]:
    name = pack or default_pack()
    objects = _guarded(_object_view, pack)
    links = _guarded(load_link_types, _pack_dir(pack))
    actions = _guarded(load_action_types, _pack_dir(pack))
    functions = _guarded(load_functions, _pack_dir(pack))
    metrics = _guarded(_metric_view, pack)
    return {
        "pack": name,
        "counts": {
            "object_types": len(objects),
            "properties": sum(o["property_count"] for o in objects),
            "sensitive_properties": sum(o["sensitive_count"] for o in objects),
            "link_types": len(links),
            "action_types": len(actions),
            "action_tools": sum(1 for a in actions if a.kind == "tool"),
            "action_events": sum(1 for a in actions if a.kind != "tool"),
            "functions": len(functions),
            "metrics": len(metrics),
        },
        "objects_without_metrics": [o["id"] for o in objects if not o["metric_ids"]],
    }


@router.get("/objects")
def ontology_objects(pack: str | None = None) -> dict[str, Any]:
    return {"pack": pack or default_pack(), "object_types": _guarded(_object_view, pack)}


@router.get("/links")
def ontology_links(pack: str | None = None) -> dict[str, Any]:
    links = _guarded(load_link_types, _pack_dir(pack))
    return {"pack": pack or default_pack(), "link_types": [asdict(link) for link in links]}


@router.get("/actions")
def ontology_actions(pack: str | None = None) -> dict[str, Any]:
    actions = _guarded(load_action_types, _pack_dir(pack))
    return {
        "pack": pack or default_pack(),
        "action_types": [{**asdict(a), "params": list(a.params)} for a in actions],
    }


@router.get("/functions")
def ontology_functions(pack: str | None = None) -> dict[str, Any]:
    functions = _guarded(load_functions, _pack_dir(pack))
    return {"pack": pack or default_pack(), "functions": [asdict(f) for f in functions]}


@router.get("/metrics")
def ontology_metrics(pack: str | None = None) -> dict[str, Any]:
    return {"pack": pack or default_pack(), "metrics": _guarded(_metric_view, pack)}


@router.get("/graph")
def ontology_graph(pack: str | None = None) -> dict[str, Any]:
    """Nodes + edges for a rendered map. Node degree is computed here, not client-side."""
    objects = _guarded(_object_view, pack)
    links = _guarded(load_link_types, _pack_dir(pack))
    degree: dict[str, int] = {o["id"]: 0 for o in objects}
    edges: list[dict[str, Any]] = []
    for link in links:
        degree[link.from_object] = degree.get(link.from_object, 0) + 1
        degree[link.to_object] = degree.get(link.to_object, 0) + 1
        edges.append(
            {
                "id": link.id,
                "source": link.from_object,
                "target": link.to_object,
                "source_property": link.from_property,
                "target_property": link.to_property,
                "cardinality": link.cardinality,
            }
        )
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
    return {"pack": pack or default_pack(), "nodes": nodes, "edges": edges}


def register_ontology_routes(app, dependencies: list[Any] | None = None) -> None:
    """Mount the ontology read API. ``dependencies`` carries the pack's role gate."""
    app.include_router(router, dependencies=dependencies or [])


__all__ = ["register_ontology_routes", "router"]
