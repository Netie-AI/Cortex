"""Read Constructor fetch/place slices from the DMS DuckDB warehouse."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _json_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _jsonable(v) for k, v in row.items()} for row in rows]

from CortexOS.dms.warehouse_db import (
    KNOWN_TABLES,
    load_inventory_csv,
    preview_table,
    table_row_counts,
)

_PLACE_PREFIX = "warehouse."


def parse_place(fetch_from: str | None) -> str | None:
    raw = (fetch_from or "").strip().lower()
    if not raw:
        return None
    if raw.startswith(_PLACE_PREFIX):
        raw = raw[len(_PLACE_PREFIX) :]
    if raw in KNOWN_TABLES:
        return raw
    return None


def ensure_seeded() -> dict[str, int]:
    counts = table_row_counts()
    if any(n > 0 for n in counts.values()):
        return counts
    load_inventory_csv()
    return table_row_counts()


def fetch_slice(node: dict[str, Any]) -> dict[str, Any]:
    table = parse_place(str(node.get("fetch_from") or ""))
    point = str(node.get("data_point") or "").strip()
    if table is None:
        obj = str(node.get("object_type") or "").strip()
        if obj in KNOWN_TABLES:
            table = obj
    if table is None:
        return {
            "table": None,
            "data_point": point or None,
            "row_count": 0,
            "error": "fetch_from must be warehouse.<table>",
        }
    ensure_seeded()
    try:
        cols = [point] if point else None
        rows, total, all_cols = preview_table(table, from_row=0, to_row=20, cols=cols)
    except Exception as exc:  # noqa: BLE001 — surface warehouse miss, do not invent rows
        return {
            "table": table,
            "data_point": point or None,
            "row_count": 0,
            "error": str(exc)[:200],
        }
    if point and point not in all_cols:
        return {
            "table": table,
            "data_point": point,
            "row_count": total,
            "error": f"column {point!r} not on {table}",
            "columns": all_cols,
        }
    return {
        "table": table,
        "data_point": point or None,
        "row_count": total,
        "preview": _json_rows(rows[:8]),
        "columns": all_cols,
    }
