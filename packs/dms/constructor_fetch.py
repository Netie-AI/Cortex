"""Resolve Constructor inspect fields onto warehouse rows and the stream registry.

C2: lives in the pack, not CortexOS. constructor_graph only stamps context_key;
this module is what actually reads DuckDB /dms/streams.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from CortexOS.dms.warehouse_db import KNOWN_TABLES, preview_table
from CortexOS.ontology.registry import load_action_types, load_object_types
from packs.dms.ontology.registry import PACK_DIR

_FETCH_ALIAS = {
    "warehouse.inventory": "inventory",
    "warehouse.suppliers": "suppliers",
    "warehouse.locations": "locations",
    "warehouse.shipments": "shipments",
    "warehouse.transactions": "transactions",
    "warehouse.alerts": "alerts",
}

_CHAT_ACTIONS = frozenset({"export_pptx", "item.intake", "agent.checked"})


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def catalog() -> dict[str, Any]:
    """Agent-visible ontology objects + the three Constructor-selectable actions."""
    objects: dict[str, Any] = {}
    for obj in load_object_types(PACK_DIR):
        objects[obj.id] = {
            "points": {p.name: p.type for p in obj.properties if p.agent_visible},
            "primary_key": obj.primary_key,
            "description": obj.description,
        }
    actions = []
    for action in load_action_types(PACK_DIR):
        if action.id not in _CHAT_ACTIONS:
            continue
        actions.append(
            {
                "id": action.id,
                "kind": action.kind,
                "object_type": action.object_type,
                "requires_confirm": action.requires_confirm,
                "required_role": action.required_role,
            }
        )
    return {
        "objects": objects,
        "actions": [row["id"] for row in actions],
        "action_meta": actions,
        "tiers": ["T0", "T1"],
        "fetch_places": sorted(_FETCH_ALIAS.keys()),
    }


def resolve_table(object_type: str | None, fetch_from: str | None) -> str | None:
    if fetch_from:
        key = str(fetch_from).strip()
        if key in _FETCH_ALIAS:
            return _FETCH_ALIAS[key]
        if key in KNOWN_TABLES:
            return key
        if "." in key:
            last = key.rsplit(".", 1)[-1]
            if last in KNOWN_TABLES:
                return last
    if object_type in KNOWN_TABLES:
        return str(object_type)
    return None


def fetch_slice(
    *,
    object_type: str | None = None,
    data_point: str | None = None,
    data_type: str | None = None,
    fetch_from: str | None = None,
    stream: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    table = resolve_table(object_type, fetch_from)
    by_id = {obj.id: obj for obj in load_object_types(PACK_DIR)}
    cols: list[str] | None = None
    if table and table in by_id:
        visible = [p.name for p in by_id[table].properties if p.agent_visible]
        if data_point and data_point in visible:
            pk = by_id[table].primary_key
            cols = [pk, data_point] if pk != data_point else [data_point]
        else:
            cols = visible[:8]
        if data_point and table in by_id and not data_type:
            for prop in by_id[table].properties:
                if prop.name == data_point:
                    data_type = prop.type
                    break
    rows: list[dict[str, Any]] = []
    total = 0
    all_cols: list[str] = []
    error: str | None = None
    if table:
        try:
            rows, total, all_cols = preview_table(table, from_row=0, to_row=max(1, min(limit, 100)), cols=cols)
        except Exception as exc:  # noqa: BLE001 — surface warehouse miss to Constructor audit
            error = str(exc)
    streams = None
    if stream:
        from packs.dms.streams import registry

        streams = registry.list_streams()
    return {
        "object_type": object_type,
        "data_point": data_point,
        "data_type": data_type,
        "fetch_from": fetch_from,
        "table": table,
        "rows": _jsonable(rows),
        "row_count": total,
        "columns": all_cols,
        "stream": bool(stream),
        "streams": streams,
        "error": error,
    }
