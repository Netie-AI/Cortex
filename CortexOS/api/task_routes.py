"""F5 task gate API routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from CortexOS.security.auth_port import Principal, require_role

# #263: each route writes a task event or verdict under an actor. The actor is
# the authenticated caller; the ``actor`` body field stays on the wire, unread.


class GateCheckRequest(BaseModel):
    event_id: str
    task_id: str
    filled_template: dict[str, Any] = {}
    actor: str = "user"  # wire only (#263): ignored; the actor is the authenticated caller


class ChooseTaskRequest(BaseModel):
    message_id: str | None = None
    thread_id: str | None = None
    task_id: str
    filled_template: dict[str, Any] = {}
    intent: str | None = None
    actor: str = "user"  # wire only (#263): ignored; the actor is the authenticated caller
    accepted: bool = True


class AcknowledgeRequest(BaseModel):
    event_id: str
    actor: str = "steward"  # wire only (#263): ignored; the actor is the authenticated caller


def _verdict_dict(verdict) -> dict[str, Any]:
    return {
        "status": verdict.status,
        "violations": verdict.violations,
        "executable": verdict.executable,
        "event_id": verdict.event_id,
    }


def register_task_routes(app: Any, *, gate_auth: Any = None) -> None:
    """Register the F5 task routes. Every route needs a credential (#263).

    ``gate_auth`` gates ``gate/check`` only: DMS calls it on every mutation (its
    F5 compliance gate, docs/dms/packets/CORTEX_TO_DMS_C4_SUBMIT.md section 5)
    with the key it is configured with, by default the published demo viewer
    key. ``create_app`` passes the DMS pack's viewer dependency, as for
    ``/dms/query``. Without it the route uses the engine port at steward, which
    refuses that key: the fallback is the stricter gate. ``choose`` and
    ``gate/acknowledge`` are not DMS calls and stay on the engine port at steward.
    """
    gate = gate_auth if gate_auth is not None else require_role("steward")
    steward = require_role("steward")
    router = APIRouter(prefix="/dms/tasks", tags=["tasks"])

    @router.post("/gate/check")
    def gate_check(req: GateCheckRequest, caller: Principal = Depends(gate)):
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
    def choose_task(req: ChooseTaskRequest, caller: Principal = Depends(steward)):
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
    def gate_acknowledge(req: AcknowledgeRequest, caller: Principal = Depends(steward)):
        from packs.dms.tasks.gate import acknowledge_event

        try:
            verdict = acknowledge_event(req.event_id, actor=caller.actor)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "verdict": _verdict_dict(verdict)}

    app.include_router(router)
