"""Demo API + UI. Cloud Run entrypoint (PORT 8080)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from night_shift import MODEL_FLASH, __version__
from night_shift.pipeline import DEMO_INBOX, NightShift
from night_shift.registry import list_agents

NS = NightShift()
STATIC = Path(__file__).parent / "static"

api = FastAPI(title="Night Shift", version=__version__)
if STATIC.is_dir():
    api.mount("/assets", StaticFiles(directory=STATIC), name="assets")


class InboxBody(BaseModel):
    inbox: str = DEMO_INBOX


@api.get("/")
def index():
    page = STATIC / "index.html"
    if page.is_file():
        return FileResponse(page)
    return JSONResponse({"ok": True, "service": "night-shift"})


@api.get("/api/health")
def health():
    return {
        "ok": True,
        "model": MODEL_FLASH,
        "gcp_project": os.environ.get("GOOGLE_CLOUD_PROJECT") or "",
        "cloud_run": os.environ.get("K_SERVICE") or "",
        "adk": _adk_ok(),
    }


@api.get("/api/proof")
def proof():
    """What the demo video must show: Gemini 3.5, ADK, a GCP service."""
    return {
        "gemini_model": MODEL_FLASH,
        "agent_framework": "google-adk Workflow (sequential + parallel + loop)",
        "gcp_service": os.environ.get("K_SERVICE") and "Cloud Run" or "local (set K_SERVICE on Cloud Run)",
        "project": os.environ.get("GOOGLE_CLOUD_PROJECT") or "(unset)",
        "region": os.environ.get("GOOGLE_CLOUD_REGION") or os.environ.get("FUNCTION_REGION") or "",
        "run_url": os.environ.get("NIGHT_SHIFT_PUBLIC_URL") or "",
    }


@api.get("/api/registry")
def registry():
    return {"agents": list_agents()}


@api.get("/api/memory")
def memory():
    return NS.memory.dump()


@api.get("/api/evolve")
def evolve():
    return {
        "score": NS.evolve.score,
        "rewrites": NS.evolve.rewrites,
        "prompts": NS.evolve.prompts,
        "history": NS.evolve.history,
    }


@api.post("/api/runs")
def start_run(body: InboxBody):
    run_id = uuid.uuid4().hex[:8]
    run = NS.start(body.inbox or DEMO_INBOX, run_id)
    NS.critic_until_pass(run)
    return _view(run)


@api.post("/api/runs/{run_id}/approve")
def approve(run_id: str):
    if run_id not in NS.runs:
        raise HTTPException(404, "run not found")
    return _view(NS.approve(run_id))


@api.post("/api/runs/{run_id}/crash")
def crash(run_id: str):
    if run_id not in NS.runs:
        raise HTTPException(404, "run not found")
    try:
        return _view(NS.crash_before_commit(run_id))
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@api.post("/api/runs/{run_id}/resume")
def resume(run_id: str):
    if run_id not in NS.runs:
        raise HTTPException(404, "run not found")
    return _view(NS.resume_place(run_id))


@api.get("/api/runs/{run_id}")
def get_run(run_id: str):
    if run_id not in NS.runs:
        raise HTTPException(404, "run not found")
    return _view(NS.runs[run_id])


@api.get("/api/ledger")
def ledger():
    return NS.ledger.dump()


def _view(run) -> dict:
    return {
        "id": run.id,
        "step": run.step,
        "draft": run.draft,
        "parallel": run.parallel,
        "critic_rounds": run.critic_rounds,
        "critic_notes": run.critic_notes,
        "approved": run.approved,
        "crashed": run.crashed,
        "result": run.result,
        "events": run.events,
        "invocation_id": run.invocation_id,
        "placed_count": NS.ledger.placed_count(),
    }


def _adk_ok() -> bool:
    try:
        from night_shift.agent import root_agent

        return root_agent is not None
    except Exception:
        return False


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run("night_shift.server:api", host="0.0.0.0", port=port, reload=False)


app = api
