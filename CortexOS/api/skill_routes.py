"""F6 skill capture API routes (F7 remainder: RBAC on mutating endpoints)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/dms/skills", tags=["skills"])


class CompleteEventRequest(BaseModel):
    event_id: str
    outcome: str
    trigger_text: str | None = None


class CaptureConfigRequest(BaseModel):
    enabled: bool


@router.get("")
def list_captured_skills(
    active_only: bool = True,
    caller: Caller = Depends(require_role("viewer")),
) -> list[dict[str, Any]]:
    from packs.dms.skills.capture import list_skills

    _ = caller
    return list_skills(active_only=active_only)


@router.get("/config")
def get_capture_config(caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from packs.dms.skills.capture import capture_enabled

    _ = caller
    return {"capture_enabled": capture_enabled()}


@router.post("/config")
def set_capture_config(
    req: CaptureConfigRequest,
    caller: Caller = Depends(require_role("steward")),
) -> dict[str, Any]:
    from packs.dms.skills.capture import set_capture_enabled

    _ = caller
    return {"capture_enabled": set_capture_enabled(req.enabled)}


@router.post("/complete")
def complete_task_event(
    req: CompleteEventRequest,
    caller: Caller = Depends(require_role("steward")),
) -> dict[str, Any]:
    from packs.dms.skills.capture import complete_event

    try:
        return complete_event(
            req.event_id,
            req.outcome,
            trigger_text=req.trigger_text,
            actor=caller.actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{skill_id}/deactivate")
def deactivate(
    skill_id: str,
    caller: Caller = Depends(require_role("steward")),
) -> dict[str, Any]:
    from packs.dms.skills.capture import deactivate_skill

    if not deactivate_skill(skill_id, actor=caller.actor):
        raise HTTPException(status_code=404, detail="skill not found or already inactive")
    return {"ok": True, "skill_id": skill_id}


def register_skill_routes(app) -> None:
    app.include_router(router)
