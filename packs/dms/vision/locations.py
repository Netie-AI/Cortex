"""Location tree + QR label generation (V0)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import qrcode

from packs.dms.vision.warehouse_store import (
    create_location,
    get_location_by_code,
    list_items_at_location,
    list_location_tree,
)


def build_location(
    *,
    kind: str,
    code: str,
    parent_code: str | None = None,
    capacity_volume: float | None = None,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    parent_id = None
    if parent_code:
        parent = get_location_by_code(parent_code, tenant_id=tenant_id, db_path=db_path)
        if parent is None:
            raise ValueError(f"parent location not found: {parent_code}")
        parent_id = parent.id
    loc = create_location(
        kind=kind,
        code=code,
        parent_id=parent_id,
        capacity_volume=capacity_volume,
        tenant_id=tenant_id,
        db_path=db_path,
    )
    return {
        "id": loc.id,
        "kind": loc.kind,
        "code": loc.code,
        "qr_token": loc.qr_token,
        "parent_id": loc.parent_id,
        "capacity_volume": loc.capacity_volume,
    }


def location_tree_with_items(
    *,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    tree = list_location_tree(tenant_id=tenant_id, db_path=db_path)

    def attach(node: dict[str, Any]) -> dict[str, Any]:
        items = list_items_at_location(node["id"], tenant_id=tenant_id, db_path=db_path)
        node["items"] = [
            {
                "id": i.id,
                "sku": i.sku,
                "label": i.label,
                "photo_uri": i.photo_uri,
                "dims": i.dims,
            }
            for i in items
        ]
        node["children"] = [attach(c) for c in node.get("children", [])]
        return node

    return [attach(n) for n in tree]


def render_qr_png(qr_token: str, *, box_size: int = 8) -> bytes:
    qr = qrcode.QRCode(box_size=box_size, border=2)
    qr.add_data(qr_token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
