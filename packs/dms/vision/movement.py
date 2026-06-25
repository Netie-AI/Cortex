"""Scan-on-move flow (V0)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packs.dms.audit.ledger import append as ledger_append
from packs.dms.vision.warehouse_store import (
    get_item_by_id_or_qr,
    get_location_by_qr_token,
    record_movement,
)


def scan_move(
    *,
    item_qr_or_id: str,
    to_location_qr: str,
    actor: str = "system",
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    item = get_item_by_id_or_qr(item_qr_or_id, tenant_id=tenant_id, db_path=db_path)
    if item is None:
        raise ValueError(f"item not found: {item_qr_or_id}")

    dest = get_location_by_qr_token(to_location_qr, tenant_id=tenant_id, db_path=db_path)
    if dest is None:
        raise ValueError(f"destination not found for qr: {to_location_qr}")

    movement = record_movement(
        item_id=item.id,
        to_location_id=dest.id,
        actor=actor,
        method="scan",
        tenant_id=tenant_id,
        db_path=db_path,
    )

    ledger_entry = ledger_append(
        actor,
        "item.moved",
        {
            "item_id": item.id,
            "movement_id": movement.id,
            "from_location_id": movement.from_location_id,
            "to_location_id": movement.to_location_id,
            "to_location_code": dest.code,
            "method": "scan",
        },
        db_path=db_path,
    )

    return {
        "movement": {
            "id": movement.id,
            "item_id": movement.item_id,
            "from_location_id": movement.from_location_id,
            "to_location_id": movement.to_location_id,
            "method": movement.method,
        },
        "item_id": item.id,
        "to_location_code": dest.code,
        "ledger_seq": ledger_entry.seq,
        "ledger_event": ledger_entry.event_type,
    }
