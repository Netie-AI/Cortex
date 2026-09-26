"""HTTP surface for background workflows — Background tasks panel contract.

AirGPT proxies these under the same paths on :8765. Cortex owns the durable
store and runner; this module is a thin FastAPI registration.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from CortexOS.execution import workflow_runner, workflow_store
from CortexOS.execution.workflow_recognizer import recognize
from CortexOS.security.auth_port import require_role


class RunBody(BaseModel):
    template_id: str = ""
    workflow_id: str = ""  # AirGPT alias
    goal: str = ""
    topic: str = ""
    target: str = ""
    breadth: int = 3
    session_id: str = ""
    # Accepted for wire compatibility and ignored: the run's actor is the
    # authenticated caller (T2-CTRL-B, #263), never a value the body asserts.
    actor: str = ""
    origin: str = "api"
    cost_ceiling_myr: float | None = None
    variables: dict[str, Any] = Field(default_factory=dict)


class CancelBody(BaseModel):
    task_id: str = ""
    run_id: str = ""


class RecognizeBody(BaseModel):
    prompt: str = ""
    text: str = ""


class HardwareBody(BaseModel):
    hardware: dict[str, Any] = Field(default_factory=dict)


# T2-CTRL-B (#263): every route is gated through the engine's auth port (no
# packs import). Reads and the pure recognizer need viewer; starting, cancelling
# and resuming runs and pushing hardware need steward; wiping run history needs
# admin.
_viewer_dep = require_role("viewer")
_steward_dep = require_role("steward")
_VIEWER = [Depends(_viewer_dep)]
_STEWARD = [Depends(_steward_dep)]
_ADMIN = [Depends(require_role("admin"))]


def _reap_orphans() -> None:
    """Reconcile runs a dead engine left 'running' before any read reports them.

    ``snapshot()`` and ``resume()`` already do this; the per-task, event-stream
    and activity reads did not, so they could show a dead run as live.
    """
    workflow_runner._reap_orphans_once()


def register_workflow_routes(app: Any) -> None:
    @app.get("/api/workflows", dependencies=_VIEWER)
    async def list_workflows() -> dict[str, Any]:
        return {"ok": True, "workflows": workflow_runner.list_workflows()}

    @app.get("/api/workflows/tasks", dependencies=_VIEWER)
    async def list_tasks() -> dict[str, Any]:
        return workflow_runner.snapshot()

    @app.get("/api/workflows/task/{task_id}", dependencies=_VIEWER)
    async def get_task(task_id: str) -> dict[str, Any]:
        _reap_orphans()
        task = workflow_runner.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="unknown task")
        return {"ok": True, "task": task}

    @app.post("/api/workflows/run")
    async def run_workflow(
        request: Request, body: RunBody, caller: Any = Depends(_steward_dep)
    ) -> dict[str, Any]:
        ledger = getattr(request.app.state, "ledger", None)
        router = getattr(request.app.state, "model_router", None)
        tid = body.template_id or body.workflow_id
        result = workflow_runner.start(
            tid,
            goal=body.goal,
            topic=body.topic,
            target=body.target,
            breadth=body.breadth,
            variables=body.variables,
            origin=body.origin or "api",
            session_id=body.session_id,
            actor=str(getattr(caller, "actor", "") or ""),
            cost_ceiling_myr=body.cost_ceiling_myr,
            router=router,
            ledger=ledger,
            hardware=workflow_runner.get_hardware(),
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "start failed")
        return result

    @app.post("/api/workflows/cancel", dependencies=_STEWARD)
    async def cancel_workflow(body: CancelBody) -> dict[str, Any]:
        tid = body.task_id or body.run_id
        result = workflow_runner.cancel(tid)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error") or "cancel failed")
        return result

    @app.post("/api/workflows/resume", dependencies=_STEWARD)
    async def resume_workflow(request: Request, body: CancelBody) -> dict[str, Any]:
        """Re-run a finished run under its id; journaled nodes replay for free."""
        ledger = getattr(request.app.state, "ledger", None)
        router = getattr(request.app.state, "model_router", None)
        result = workflow_runner.resume(
            body.task_id or body.run_id,
            router=router,
            ledger=ledger,
            hardware=workflow_runner.get_hardware(),
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "resume failed")
        return result

    @app.post("/api/workflows/clear", dependencies=_ADMIN)
    async def clear_finished() -> dict[str, Any]:
        return workflow_runner.clear_finished()

    @app.post("/api/workflows/recognize", dependencies=_VIEWER)
    async def recognize_prompt(body: RecognizeBody) -> dict[str, Any]:
        text = body.prompt or body.text or ""
        rec = recognize(text)
        return {"ok": True, **rec.as_dict()}

    @app.post("/api/workflows/hardware", dependencies=_STEWARD)
    async def push_hardware(body: HardwareBody) -> dict[str, Any]:
        workflow_runner.set_hardware(body.hardware)
        return {"ok": True, "hardware": workflow_runner.get_hardware()}

    # response_model=None: this handler is declared inside a factory, so with
    # `from __future__ import annotations` its return type reaches FastAPI as
    # ForwardRef("StreamingResponse") which pydantic cannot resolve from module
    # globals. It raised only while generating the OpenAPI schema, so the route
    # served fine and scripts/export_openapi.py was the thing that broke.
    @app.get("/api/workflows/task/{task_id}/events", response_model=None, dependencies=_VIEWER)
    async def task_events(task_id: str) -> StreamingResponse:
        _reap_orphans()
        if not workflow_store.get_run(task_id):
            raise HTTPException(status_code=404, detail="unknown task")

        def _gen():
            for event in workflow_store.stream(task_id):
                yield f"data: {json.dumps(event, default=str)}\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")
