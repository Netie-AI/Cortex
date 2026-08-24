"""HTTP surface for background workflows — Background tasks panel contract.

AirGPT proxies these under the same paths on :8765. Cortex owns the durable
store and runner; this module is a thin FastAPI registration.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from CortexOS.execution import workflow_runner, workflow_store
from CortexOS.execution.workflow_recognizer import recognize


class RunBody(BaseModel):
    template_id: str = ""
    workflow_id: str = ""  # AirGPT alias
    goal: str = ""
    topic: str = ""
    target: str = ""
    breadth: int = 3
    session_id: str = ""
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


def register_workflow_routes(app: Any) -> None:
    @app.get("/api/workflows")
    async def list_workflows() -> dict[str, Any]:
        return {"ok": True, "workflows": workflow_runner.list_workflows()}

    @app.get("/api/workflows/tasks")
    async def list_tasks() -> dict[str, Any]:
        return workflow_runner.snapshot()

    @app.get("/api/workflows/task/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        task = workflow_runner.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="unknown task")
        return {"ok": True, "task": task}

    @app.post("/api/workflows/run")
    async def run_workflow(request: Request, body: RunBody) -> dict[str, Any]:
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
            actor=body.actor,
            cost_ceiling_myr=body.cost_ceiling_myr,
            router=router,
            ledger=ledger,
            hardware=workflow_runner.get_hardware(),
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "start failed")
        return result

    @app.post("/api/workflows/cancel")
    async def cancel_workflow(body: CancelBody) -> dict[str, Any]:
        tid = body.task_id or body.run_id
        result = workflow_runner.cancel(tid)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error") or "cancel failed")
        return result

    @app.post("/api/workflows/resume")
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

    @app.post("/api/workflows/clear")
    async def clear_finished() -> dict[str, Any]:
        return workflow_runner.clear_finished()

    @app.post("/api/workflows/recognize")
    async def recognize_prompt(body: RecognizeBody) -> dict[str, Any]:
        text = body.prompt or body.text or ""
        rec = recognize(text)
        return {"ok": True, **rec.as_dict()}

    @app.post("/api/workflows/hardware")
    async def push_hardware(body: HardwareBody) -> dict[str, Any]:
        workflow_runner.set_hardware(body.hardware)
        return {"ok": True, "hardware": workflow_runner.get_hardware()}

    # response_model=None: this handler is declared inside a factory, so with
    # `from __future__ import annotations` its return type reaches FastAPI as
    # ForwardRef("StreamingResponse") which pydantic cannot resolve from module
    # globals. It raised only while generating the OpenAPI schema, so the route
    # served fine and scripts/export_openapi.py was the thing that broke.
    @app.get("/api/workflows/task/{task_id}/events", response_model=None)
    async def task_events(task_id: str) -> StreamingResponse:
        if not workflow_store.get_run(task_id):
            raise HTTPException(status_code=404, detail="unknown task")

        def _gen():
            for event in workflow_store.stream(task_id):
                yield f"data: {json.dumps(event, default=str)}\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")
