"""S0 — OpenDMS streaming intake API (register streams, post events → bronze)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/dms/streams", tags=["streams"])

MAX_EVENTS_PER_REQUEST = 1000


class CreateStreamRequest(BaseModel):
    stream_id: str
    name: str = ""
    schema_hint: str = ""


class EventsRequest(BaseModel):
    events: Optional[list[dict]] = None
    event: Optional[dict] = None


@router.post("")
def create_stream(req: CreateStreamRequest, caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.streams import registry

    try:
        return registry.create_stream(req.stream_id, name=req.name,
                                      created_by=caller.actor, schema_hint=req.schema_hint)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_streams(caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from packs.dms.streams import registry

    _ = caller
    return {"streams": registry.list_streams()}


@router.post("/{stream_id}/events")
def post_events(stream_id: str, req: EventsRequest,
                caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.streams import buffer, registry

    if not registry.valid_stream_id(stream_id):
        raise HTTPException(status_code=400, detail="invalid stream_id")
    events = req.events if req.events is not None else ([req.event] if req.event else [])
    if not events:
        raise HTTPException(status_code=400, detail="no events provided")
    if len(events) > MAX_EVENTS_PER_REQUEST:
        raise HTTPException(status_code=413,
                            detail=f"max {MAX_EVENTS_PER_REQUEST} events per request")
    # auto-register on first use so producers don't need a separate call
    if registry.get_stream(stream_id) is None:
        registry.create_stream(stream_id, created_by=caller.actor)
    try:
        return buffer.append_events(stream_id, events)
    except buffer.BackpressureError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/{stream_id}/flush")
def flush_stream(stream_id: str, caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.streams import buffer

    _ = caller
    return {"flushed": buffer.flush(stream_id)}


@router.get("/{stream_id}/preview")
def preview_stream(stream_id: str, limit: int = 50,
                   caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from packs.dms.lakehouse import tables as lt
    from packs.dms.streams.buffer import _table

    _ = caller
    try:
        rows = lt.read("bronze", _table(stream_id), limit=min(max(limit, 1), 500))
    except Exception:  # noqa: BLE001 — stream not yet flushed
        rows = []
    return {"stream_id": stream_id, "rows": rows, "row_count": len(rows)}


def register_stream_routes(app) -> None:
    app.include_router(router)
