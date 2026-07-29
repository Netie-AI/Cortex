"""F5 task gate API routes."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/dms/tasks", tags=["tasks"])


class GateCheckRequest(BaseModel):
    event_id: str
    task_id: str
    filled_template: dict[str, Any] = {}
    actor: str = "user"


class ChooseTaskRequest(BaseModel):
    message_id: str | None = None
    thread_id: str | None = None
    task_id: str
    filled_template: dict[str, Any] = {}
    intent: str | None = None
    actor: str = "user"
    accepted: bool = True


class AcknowledgeRequest(BaseModel):
    event_id: str
    actor: str = "steward"


def _verdict_dict(verdict) -> dict[str, Any]:
    return {
        "status": verdict.status,
        "violations": verdict.violations,
        "executable": verdict.executable,
        "event_id": verdict.event_id,
    }


@router.post("/gate/check")
def gate_check(req: GateCheckRequest):
    from packs.dms.tasks.gate import check_task

    try:
        verdict = check_task(
            req.event_id,
            req.task_id,
            req.filled_template,
            actor=req.actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _verdict_dict(verdict)


@router.post("/choose")
def choose_task(req: ChooseTaskRequest):
    from packs.dms.tasks.gate import check_task, create_task_event
    from packs.dms.tasks.suggest import record_choice

    if req.accepted:
        record_choice(req.task_id, True, req.actor)
    event_id = create_task_event(
        message_id=req.message_id,
        thread_id=req.thread_id,
        task_id=req.task_id,
        intent=req.intent,
        filled_template=req.filled_template,
        actor=req.actor,
    )
    verdict = check_task(
        event_id,
        req.task_id,
        req.filled_template,
        actor=req.actor,
    )
    return {"ok": True, "event_id": event_id, "verdict": _verdict_dict(verdict)}


@router.post("/gate/acknowledge")
def gate_acknowledge(req: AcknowledgeRequest):
    from packs.dms.tasks.gate import acknowledge_event

    try:
        verdict = acknowledge_event(req.event_id, actor=req.actor)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "verdict": _verdict_dict(verdict)}


def register_task_routes(app) -> None:
    app.include_router(router)
