"""F6 skill capture API routes."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/dms/skills", tags=["skills"])


class CompleteEventRequest(BaseModel):
    event_id: str
    outcome: str
    trigger_text: Optional[str] = None
    actor: str = "user"


class CaptureConfigRequest(BaseModel):
    enabled: bool


@router.get("")
def list_captured_skills(active_only: bool = True) -> List[Dict[str, Any]]:
    from packs.dms.skills.capture import list_skills

    return list_skills(active_only=active_only)


@router.get("/config")
def get_capture_config() -> Dict[str, Any]:
    from packs.dms.skills.capture import capture_enabled

    return {"capture_enabled": capture_enabled()}


@router.post("/config")
def set_capture_config(req: CaptureConfigRequest) -> Dict[str, Any]:
    from packs.dms.skills.capture import set_capture_enabled

    return {"capture_enabled": set_capture_enabled(req.enabled)}


@router.post("/complete")
def complete_task_event(req: CompleteEventRequest) -> Dict[str, Any]:
    from packs.dms.skills.capture import complete_event

    try:
        return complete_event(
            req.event_id,
            req.outcome,
            trigger_text=req.trigger_text,
            actor=req.actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{skill_id}/deactivate")
def deactivate(skill_id: str, actor: str = "steward") -> Dict[str, Any]:
    from packs.dms.skills.capture import deactivate_skill

    if not deactivate_skill(skill_id, actor=actor):
        raise HTTPException(status_code=404, detail="skill not found or already inactive")
    return {"ok": True, "skill_id": skill_id}


def register_skill_routes(app) -> None:
    app.include_router(router)
