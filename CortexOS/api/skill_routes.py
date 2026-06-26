"""F6 skill capture API routes."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/dms/skills", tags=["skills"])


class DeactivateSkillRequest(BaseModel):
    actor: str = "steward"


@router.get("/capture-status")
def capture_status() -> Dict[str, Any]:
    from packs.dms.skills.capture import is_capture_enabled

    return {
        "enabled": is_capture_enabled(),
        "note": "Skill capture is opt-in. Enable with DMS_SKILL_CAPTURE_ENABLED=1.",
    }


@router.get("")
def list_captured_skills(active_only: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    from packs.dms.skills.capture import is_capture_enabled, list_skills

    return {
        "capture_enabled": is_capture_enabled(),
        "skills": list_skills(active_only=active_only),
    }


@router.post("/{skill_id}/deactivate")
def deactivate_captured_skill(skill_id: str, req: DeactivateSkillRequest):
    from packs.dms.skills.capture import deactivate_skill

    if not deactivate_skill(skill_id, actor=req.actor):
        raise HTTPException(status_code=404, detail=f"skill not found: {skill_id}")
    return {"ok": True, "skill_id": skill_id, "active": False}


def register_skill_routes(app) -> None:
    app.include_router(router)
