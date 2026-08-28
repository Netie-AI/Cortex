"""HTTP surface for the app store — AirGPT's "Add app" contract.

Import any zip (base64 body — no multipart dependency), get a gated draft or
blocked record back; approve/reject/rescan complete the human loop.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from CortexOS.execution import app_package, app_store, humanize


class ImportBody(BaseModel):
    name: str | None = None
    zip_base64: str = Field(..., min_length=4)


class ImportFolderBody(BaseModel):
    path: str = Field(..., min_length=1)
    name: str | None = None


class RejectBody(BaseModel):
    reason: str = "rejected"


def _found(record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        raise HTTPException(status_code=404, detail=humanize.explain("unknown_app"))
    return record


def _humanize(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attach plain-English versions of anything the user might have to act on."""
    if not record:
        return record
    out = dict(record)
    reasons = out.get("reasons") or []
    if reasons:
        out["explained_reasons"] = humanize.explain_all([str(r) for r in reasons])
    if out.get("last_error"):
        out["explained_error"] = humanize.explain(str(out["last_error"]))
    if out.get("manifest"):
        # Approval-screen copy: people approve what they understand.
        out["about"] = app_package.describe(out["manifest"], out.get("findings") or [])
    return out


def _fail(status: int, code: str) -> None:
    """Errors reach the UI already readable, never as an engine code alone."""
    raise HTTPException(status_code=status, detail=humanize.explain(code))


def register_app_routes(app: Any) -> None:
    @app.post("/api/apps/import")
    async def import_app(body: ImportBody) -> dict[str, Any]:
        try:
            data = base64.b64decode(body.zip_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid base64: {exc}") from exc
        out = app_store.import_zip_bytes(data, name=body.name)
        if not out.get("ok"):
            _fail(400, str(out.get("error")))
        out["app"] = _humanize(out.get("app"))
        return out

    @app.post("/api/apps/import-folder")
    async def import_app_folder(body: ImportFolderBody) -> dict[str, Any]:
        """Point at a project folder — no zipping, no base64, no encoding step."""
        out = app_store.import_folder(body.path, name=body.name)
        if not out.get("ok"):
            _fail(400, str(out.get("error")))
        out["app"] = _humanize(out.get("app"))
        return out

    @app.get("/api/apps")
    async def list_apps() -> dict[str, Any]:
        app_store.init()
        return {"ok": True, "apps": [_humanize(a) for a in app_store.list_apps()]}

    @app.get("/api/apps/{app_id}")
    async def get_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        return {"ok": True, "app": _humanize(_found(app_store.get_app(app_id)))}

    @app.post("/api/apps/{app_id}/approve")
    async def approve_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.approve(app_id)
        if not out.get("ok"):
            raise HTTPException(status_code=409, detail=str(out.get("error")))
        return out

    @app.post("/api/apps/{app_id}/reject")
    async def reject_app(app_id: str, body: RejectBody | None = None) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.reject(app_id, (body.reason if body else "rejected"))
        if not out.get("ok"):
            raise HTTPException(status_code=409, detail=str(out.get("error")))
        return out

    @app.post("/api/apps/{app_id}/rescan")
    async def rescan_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.rescan(app_id)
        if not out.get("ok"):
            raise HTTPException(status_code=409, detail=str(out.get("error")))
        return out

    @app.post("/api/apps/{app_id}/start")
    async def start_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.start_app(app_id)
        if not out.get("ok"):
            _fail(409, str(out.get("error")))
        out["app"] = _humanize(out.get("app"))
        return out

    @app.post("/api/apps/{app_id}/stop")
    async def stop_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.stop_app(app_id)
        if not out.get("ok"):
            raise HTTPException(status_code=409, detail=str(out.get("error")))
        return out

    @app.post("/api/apps/{app_id}/dockerize")
    async def dockerize_app(app_id: str) -> dict[str, Any]:
        """Write a Dockerfile for an app that doesn't ship one — one click to
        make it hostable. Never overwrites an author's own Dockerfile."""
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.dockerize(app_id)
        if not out.get("ok"):
            _fail(409, str(out.get("error")))
        out["app"] = _humanize(out.get("app"))
        return out

    @app.delete("/api/apps/{app_id}")
    async def delete_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        return {"ok": app_store.delete_app(app_id)}
