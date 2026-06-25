"""Per-bin occupied / free volume accounting (V1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packs.dms.vision.dimension import item_volume
from packs.dms.vision.warehouse_store import (
    get_location_by_id,
    list_items_at_location,
)


def location_space(
    location_id: str,
    *,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    loc = get_location_by_id(location_id, tenant_id=tenant_id, db_path=db_path)
    if loc is None:
        raise ValueError(f"location not found: {location_id}")

    items = list_items_at_location(location_id, tenant_id=tenant_id, db_path=db_path)
    item_rows: list[dict[str, Any]] = []
    occupied = 0.0

    for item in items:
        row = {"id": item.id, "sku": item.sku, "label": item.label, "dims": item.dims}
        if item.dims:
            vol = item_volume(item.dims)
            row["volume"] = round(vol, 6)
            occupied += vol
        item_rows.append(row)

    capacity = loc.capacity_volume
    if capacity is None:
        free = None
    else:
        free = round(max(capacity - occupied, 0.0), 6)

    return {
        "location_id": loc.id,
        "location_code": loc.code,
        "capacity_volume": capacity,
        "occupied_volume": round(occupied, 6),
        "free_volume": free,
        "unit": "m3",
        "items": item_rows,
    }
