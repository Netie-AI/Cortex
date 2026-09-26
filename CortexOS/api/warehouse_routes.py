"""Warehouse V0 API route registration."""

from typing import Any

from fastapi import Depends, Request
from pydantic import BaseModel, Field

from CortexOS.security.auth_port import Principal, require_role


class CreateLocationRequest(BaseModel):
    kind: str = Field(description="zone | rack | bin")
    code: str
    parent_code: str | None = None
    capacity_volume: float | None = None
    tenant_id: str = "default"


class IntakeRequest(BaseModel):
    sku: str
    label: str
    location_code: str
    photo: str = Field(description="base64-encoded image")
    actor: str = "demo_operator"  # ignored: the caller is recorded (T2-DMS, #263)
    tenant_id: str = "default"


class ScanMoveRequest(BaseModel):
    item_qr_or_id: str
    to_location_qr: str
    actor: str = "demo_operator"  # ignored: the caller is recorded (T2-DMS, #263)
    tenant_id: str = "default"


class ConfirmDimsRequest(BaseModel):
    # length/width/height as the wire already spells them — renaming
    # this field would be a breaking API change, not a style fix.
    l: float  # noqa: E741
    w: float
    h: float
    unit: str = "m"
    actor: str = "demo_operator"  # ignored: the caller is recorded (T2-DMS, #263)
    tenant_id: str = "default"
    gate_approved: bool = False  # overriding the capacity gate needs admin (T2-DMS, #263)


class EstimateDimsRequest(BaseModel):
    photo: str = Field(description="base64-encoded image")
    depth_source: str | None = None
    depth_map: str | None = Field(default=None, description="base64 depth map for lidar path")


# T2-DMS (#263): gated through the engine's auth port. Reads need viewer.
# Creating locations, intake, moves, dimension estimates (a step of intake that
# takes an untrusted upload) and dimension confirmation need steward, and the
# ledger actor is the authenticated caller, never a name from the request body.
# Overriding the capacity gate on confirm-dims is an approval, so it needs admin.
_VIEWER = [Depends(require_role("viewer"))]
_STEWARD = require_role("steward")
_STEWARD_ONLY = [Depends(_STEWARD)]
_ADMIN = require_role("admin")


def register_warehouse_routes(app: Any) -> None:
    from fastapi import HTTPException, Query
    from fastapi.responses import Response

    from packs.dms.vision import dimension, intake, locations, movement, space
    from packs.dms.vision.warehouse_store import (
        RLSViolationError,
        default_db_path,
        get_location_by_code,
    )

    @app.post("/dms/warehouse/locations", dependencies=_STEWARD_ONLY)
    async def warehouse_create_location(body: CreateLocationRequest) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        try:
            return locations.build_location(
                kind=body.kind,
                code=body.code,
                parent_code=body.parent_code,
                capacity_volume=body.capacity_volume,
                tenant_id=body.tenant_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/dms/warehouse/locations/tree", dependencies=_VIEWER)
    async def warehouse_location_tree(tenant_id: str = Query("default")) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        return {"tree": locations.location_tree_with_items(tenant_id=tenant_id)}

    @app.get("/dms/warehouse/locations/{location_id}/qr-label", dependencies=_VIEWER)
    async def warehouse_qr_label(location_id: str, tenant_id: str = Query("default")) -> Response:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        from packs.dms.vision.warehouse_store import _connect, init_warehouse_schema

        con = _connect(default_db_path())
        try:
            init_warehouse_schema(con)
            row = con.execute(
                "SELECT qr_token FROM dms_locations WHERE id = ? AND tenant_id = ?",
                (location_id, tenant_id),
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise HTTPException(status_code=404, detail="location not found")
        png = locations.render_qr_png(row["qr_token"])
        return Response(content=png, media_type="image/png")

    @app.post("/dms/items/intake")
    async def warehouse_intake(
        body: IntakeRequest, caller: Principal = Depends(_STEWARD)
    ) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        try:
            return intake.intake_item(
                sku=body.sku,
                label=body.label,
                location_code=body.location_code,
                photo_b64=body.photo,
                actor=caller.actor,
                tenant_id=body.tenant_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RLSViolationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/dms/movements/scan")
    async def warehouse_scan_move(
        body: ScanMoveRequest, caller: Principal = Depends(_STEWARD)
    ) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        try:
            return movement.scan_move(
                item_qr_or_id=body.item_qr_or_id,
                to_location_qr=body.to_location_qr,
                actor=caller.actor,
                tenant_id=body.tenant_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RLSViolationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/dms/items/estimate-dims", dependencies=_STEWARD_ONLY)
    async def warehouse_estimate_dims(body: EstimateDimsRequest) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        import base64
        import os

        from packs.dms.security.intake_policy import check_photo

        max_mb = float(os.environ.get("DMS_INGEST_MAX_MB", "25"))
        max_bytes = int(max_mb * 1024 * 1024)

        try:
            photo = base64.b64decode(body.photo, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid base64 photo") from exc
        if len(photo) > max_bytes:
            raise HTTPException(status_code=413, detail="photo exceeds size budget")
        _guard = check_photo(photo)
        if not _guard.ok:
            raise HTTPException(status_code=415, detail=f"photo rejected: {_guard.reason}")
        depth_map = None
        if body.depth_map:
            try:
                depth_map = base64.b64decode(body.depth_map, validate=True)
            except Exception as exc:
                raise HTTPException(status_code=400, detail="invalid base64 depth_map") from exc
            if len(depth_map) > max_bytes:
                raise HTTPException(status_code=413, detail="depth_map exceeds size budget")
            # Same magic-byte rail as photo — no unguarded binary sibling on this path.
            _dguard = check_photo(depth_map)
            if not _dguard.ok:
                raise HTTPException(
                    status_code=415, detail=f"depth_map rejected: {_dguard.reason}",
                )
        try:
            suggestion = dimension.estimate_dims(
                photo,
                depth_source=body.depth_source,
                depth_map=depth_map,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"suggested_dims": suggestion.to_dict()}

    @app.post("/dms/items/{item_id}/confirm-dims")
    async def warehouse_confirm_dims(
        item_id: str,
        body: ConfirmDimsRequest,
        request: Request,
        caller: Principal = Depends(_STEWARD),
    ) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        if body.gate_approved:
            # The gate override is an approval: re-authorize at admin before
            # anything is written, and record the admin as the actor.
            caller = await _ADMIN(request)
        try:
            return intake.confirm_item_dims(
                item_id=item_id,
                l=body.l,
                w=body.w,
                h=body.h,
                unit=body.unit,
                actor=caller.actor,
                tenant_id=body.tenant_id,
                gate_approved=body.gate_approved,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RLSViolationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/dms/locations/{location_id}/space", dependencies=_VIEWER)
    async def warehouse_location_space(
        location_id: str,
        tenant_id: str = Query("default"),
    ) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        try:
            return space.location_space(location_id, tenant_id=tenant_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/dms/warehouse/locations/by-code/{code}", dependencies=_VIEWER)
    async def warehouse_location_by_code(code: str, tenant_id: str = Query("default")) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        loc = get_location_by_code(code, tenant_id=tenant_id)
        if loc is None:
            raise HTTPException(status_code=404, detail="location not found")
        return {
            "id": loc.id,
            "code": loc.code,
            "kind": loc.kind,
            "qr_token": loc.qr_token,
        }
