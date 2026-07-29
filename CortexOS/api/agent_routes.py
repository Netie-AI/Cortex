"""S1 — OpenDMS watcher-agent API (hire agents, run, approve/reject publishes)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/dms/agents", tags=["agents"])


class CreateAgentRequest(BaseModel):
    agent_id: str
    name: str = ""
    role_label: str = "analyst"
    detector_cfg: dict
    context_question: str = ""
    approver_role: str = "steward"


class RejectRequest(BaseModel):
    reason: str = ""


@router.post("")
def create_agent(req: CreateAgentRequest, caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.agents import registry

    try:
        return registry.create_agent(
            req.agent_id, name=req.name, role_label=req.role_label, created_by=caller.actor,
            detector_cfg=req.detector_cfg, context_question=req.context_question,
            approver_role=req.approver_role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_agents(caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from packs.dms.agents import registry

    _ = caller
    return {"agents": registry.list_agents()}


@router.post("/{agent_id}/run")
def run_agent(agent_id: str, caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.agents import employee

    try:
        return employee.run_agent(agent_id, actor=caller.actor)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs")
def list_runs(agent_id: str | None = None, status: str | None = None,
              caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from packs.dms.agents import registry

    _ = caller
    return {"runs": registry.list_runs(agent_id, status=status)}


@router.post("/runs/{run_id}/approve")
def approve_run(run_id: str, caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.agents import employee

    try:
        return employee.approve_run(run_id, approver=caller.actor)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/reject")
def reject_run(run_id: str, req: RejectRequest,
               caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.agents import employee

    try:
        return employee.reject_run(run_id, approver=caller.actor, reason=req.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def register_agent_routes(app) -> None:
    app.include_router(router)
