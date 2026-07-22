"""Photo-on-intake flow (V0) + dimension confirm (V1)."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

from packs.dms.audit.ledger import append as ledger_append
from packs.dms.security.photo_sanitize import strip_exif_gps
from packs.dms.vision.dimension import estimate_dims, item_volume
from packs.dms.vision.space import location_space
from packs.dms.vision.warehouse_store import (
    create_item,
    get_item_by_id_or_qr,
    get_location_by_code,
    photos_dir,
    update_item_dims,
)


def intake_item(
    *,
    sku: str,
    label: str,
    location_code: str,
    photo_b64: str,
    actor: str = "system",
    tenant_id: str = "default",
    db_path: Path | str | None = None,
    depth_source: str | None = None,
) -> dict[str, Any]:
    loc = get_location_by_code(location_code, tenant_id=tenant_id, db_path=db_path)
    if loc is None:
        raise ValueError(f"location not found: {location_code}")

    try:
        raw = base64.b64decode(photo_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 photo") from exc

    from packs.dms.security.intake_policy import check_photo

    _guard = check_photo(raw)
    if not _guard.ok:
        # C-SEC-4: reject spoofed/executable "photos" before EXIF handling.
        raise ValueError(f"photo rejected: {_guard.reason}")

    clean = strip_exif_gps(raw)
    photo_path = photos_dir(db_path) / f"{sku.replace('/', '_')}.jpg"
    photo_path.write_bytes(clean)
    photo_uri = f"dms_photos/{photo_path.name}"

    item = create_item(
        sku=sku,
        label=label,
        location_id=loc.id,
        photo_uri=photo_uri,
        tenant_id=tenant_id,
        db_path=db_path,
    )

    ledger_entry = ledger_append(
        actor,
        "item.intake",
        {
            "item_id": item.id,
            "sku": sku,
            "location_code": location_code,
            "photo_uri": photo_uri,
        },
        db_path=db_path,
    )

    suggestion = estimate_dims(clean, depth_source=depth_source)

    return {
        "item": {
            "id": item.id,
            "sku": item.sku,
            "label": item.label,
            "current_location_id": item.current_location_id,
            "photo_uri": item.photo_uri,
            "dims": None,
        },
        "suggested_dims": suggestion.to_dict(),
        "ledger_seq": ledger_entry.seq,
        "ledger_event": ledger_entry.event_type,
    }


def confirm_item_dims(
    *,
    item_id: str,
    l: float,
    w: float,
    h: float,
    unit: str = "m",
    actor: str = "system",
    tenant_id: str = "default",
    gate_approved: bool = False,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    item = get_item_by_id_or_qr(item_id, tenant_id=tenant_id, db_path=db_path)
    if item is None:
        raise ValueError(f"item not found: {item_id}")
    if item.dims is not None:
        raise ValueError("dimensions already confirmed for this item")

    dims = {"l": l, "w": w, "h": h, "unit": unit}
    volume = item_volume(dims)

    if item.current_location_id:
        space = location_space(
            item.current_location_id,
            tenant_id=tenant_id,
            db_path=db_path,
        )
        free = space.get("free_volume")
        if free is not None and volume > free and not gate_approved:
            raise ValueError("oversize item requires compliance gate approval")

    updated = update_item_dims(item.id, dims, tenant_id=tenant_id, db_path=db_path)

    ledger_entry = ledger_append(
        actor,
        "item.dimensioned",
        {
            "item_id": updated.id,
            "sku": updated.sku,
            "dims": dims,
            "volume": round(volume, 6),
            "gate_approved": gate_approved,
        },
        db_path=db_path,
    )

    return {
        "item": {
            "id": updated.id,
            "sku": updated.sku,
            "label": updated.label,
            "dims": updated.dims,
        },
        "volume": round(volume, 6),
        "ledger_seq": ledger_entry.seq,
        "ledger_event": ledger_entry.event_type,
    }
