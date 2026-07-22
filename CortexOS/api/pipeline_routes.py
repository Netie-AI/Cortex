"""L2 — OpenDMS pipeline API (run declarative pipelines, view events, govern proposals)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/dms/pipelines", tags=["pipelines"])


class ProposeRequest(BaseModel):
    source: str
    target: str


@router.get("")
def list_pipelines(caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from packs.dms.pipelines.runner import DEFS_DIR

    _ = caller
    defs = [p.stem for p in sorted(DEFS_DIR.glob("*.yaml"))]
    return {"pipelines": defs}


@router.post("/{pipeline_id}/run")
def run_pipeline_route(pipeline_id: str, caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.pipelines.runner import PipelineError, load_pipeline, run_pipeline

    try:
        pdef = load_pipeline(pipeline_id)
        result = run_pipeline(pdef, actor=caller.actor)
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.to_dict()


@router.get("/events")
def events(limit: int = 100, caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from packs.dms.pipelines.runner import pipeline_events

    _ = caller
    return {"events": pipeline_events(limit=min(max(limit, 1), 500))}


@router.get("/proposals")
def list_proposals_route(caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from packs.dms.pipelines.propose import list_proposals

    _ = caller
    return {"proposals": list_proposals()}


@router.post("/proposals")
def create_proposal(req: ProposeRequest, caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.pipelines.propose import propose

    _ = caller
    return propose(req.source, req.target)


@router.post("/proposals/{proposal_id}/approve")
def approve_route(proposal_id: str, caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.pipelines.propose import approve_proposal

    try:
        return approve_proposal(proposal_id, approver=caller.actor)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/proposals/{proposal_id}/run")
def run_proposal_route(proposal_id: str, caller: Caller = Depends(require_role("steward"))) -> dict[str, Any]:
    from packs.dms.pipelines.propose import run_if_approved

    try:
        return run_if_approved(proposal_id, actor=caller.actor).to_dict()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def register_pipeline_routes(app) -> None:
    app.include_router(router)
