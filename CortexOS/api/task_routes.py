"""F5 task gate API routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from CortexOS.security.auth_port import Principal, require_role

router = APIRouter(prefix="/dms/tasks", tags=["tasks"])

# #263: each route writes a task event or verdict under an actor. The actor is
# the authenticated caller; a body that still sends ``actor`` is ignored.
_steward = require_role("steward")


class GateCheckRequest(BaseModel):
    event_id: str
    task_id: str
    filled_template: dict[str, Any] = {}


class ChooseTaskRequest(BaseModel):
    message_id: str | None = None
    thread_id: str | None = None
    task_id: str
    filled_template: dict[str, Any] = {}
    intent: str | None = None
    accepted: bool = True


class AcknowledgeRequest(BaseModel):
    event_id: str


def _verdict_dict(verdict) -> dict[str, Any]:
    return {
        "status": verdict.status,
        "violations": verdict.violations,
        "executable": verdict.executable,
        "event_id": verdict.event_id,
    }


@router.post("/gate/check")
def gate_check(req: GateCheckRequest, caller: Principal = Depends(_steward)):
    from packs.dms.tasks.gate import check_task

    try:
        verdict = check_task(
            req.event_id,
            req.task_id,
            req.filled_template,
            actor=caller.actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _verdict_dict(verdict)


@router.post("/choose")
def choose_task(req: ChooseTaskRequest, caller: Principal = Depends(_steward)):
    from packs.dms.tasks.gate import check_task, create_task_event
    from packs.dms.tasks.suggest import record_choice

    if req.accepted:
        record_choice(req.task_id, True, caller.actor)
    event_id = create_task_event(
        message_id=req.message_id,
        thread_id=req.thread_id,
        task_id=req.task_id,
        intent=req.intent,
        filled_template=req.filled_template,
        actor=caller.actor,
    )
    verdict = check_task(
        event_id,
        req.task_id,
        req.filled_template,
        actor=caller.actor,
    )
    return {"ok": True, "event_id": event_id, "verdict": _verdict_dict(verdict)}


@router.post("/gate/acknowledge")
def gate_acknowledge(req: AcknowledgeRequest, caller: Principal = Depends(_steward)):
    from packs.dms.tasks.gate import acknowledge_event

    try:
        verdict = acknowledge_event(req.event_id, actor=caller.actor)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "verdict": _verdict_dict(verdict)}


def register_task_routes(app) -> None:
    app.include_router(router)
