"""HTTP surface for routines — the AirGPT Routines page contract.

Cortex owns the durable store, tick loop, and governor; this module is a thin
FastAPI registration in the workflow_routes style.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from CortexOS.execution import humanize, routine_scheduler


class RoutineBody(BaseModel):
    """Everything except the goal is optional — one sentence is a valid request."""

    goal: str = ""
    name: str = ""
    prompt: str = ""
    preset: Optional[str] = None
    depth: Optional[str] = None
    interval_seconds: Optional[int] = None
    daily_cost_cap_myr: Optional[float] = None
    vars: dict[str, Any] = Field(default_factory=dict)


class DraftBody(BaseModel):
    goal: str = Field(..., min_length=1)


class RoutinePatchBody(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    preset: Optional[str] = None
    depth: Optional[str] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None
    daily_cost_cap_myr: Optional[float] = None
    vars: Optional[dict[str, Any]] = None


class PauseBody(BaseModel):
    reason: str = "user"


class FireBody(BaseModel):
    """External trigger payload — always wrapped as untrusted data before model use."""

    external_text: str = Field(..., min_length=1)
    source: str = "api_fire"


def _found(routine: dict[str, Any] | None) -> dict[str, Any]:
    if routine is None:
        raise HTTPException(status_code=404, detail="unknown routine")
    return routine


def register_routine_routes(app: Any) -> None:
    @app.get("/api/routines")
    async def list_routines() -> dict[str, Any]:
        routine_scheduler.init()
        return {
            "ok": True,
            "routines": routine_scheduler.list_routines(),
            "budget": routine_scheduler.global_budget_state(),
        }

    @app.post("/api/routines/draft")
    async def draft_routine(body: DraftBody) -> dict[str, Any]:
        """Preview what one sentence becomes — nothing is saved."""
        from CortexOS.execution import routine_composer

        return {
            "ok": True,
            "draft": routine_composer.compose(body.goal),
            "suggestions": routine_composer.SUGGESTIONS,
        }

    @app.post("/api/routines")
    async def create_routine(body: RoutineBody) -> dict[str, Any]:
        goal = (body.goal or body.prompt or "").strip()
        if not goal:
            from CortexOS.execution import humanize

            raise HTTPException(status_code=400, detail=humanize.explain("goal_required"))

        routine = routine_scheduler.create_from_goal(
            goal,
            name=body.name or None,
            preset=body.preset,
            depth=body.depth,
            interval_seconds=body.interval_seconds,
            daily_cost_cap_myr=body.daily_cost_cap_myr,
        )
        if body.vars:
            routine = routine_scheduler.update_routine(routine["id"], vars=body.vars)
        return {"ok": True, "routine": routine}

    @app.post("/api/routines/tick")
    async def tick_now() -> dict[str, Any]:
        return {"ok": True, "ran": await routine_scheduler.tick()}

    @app.post("/api/routines/pause-all")
    async def pause_all_routines() -> dict[str, Any]:
        routine_scheduler.init()
        return {"ok": True, "paused": routine_scheduler.pause_all()}

    @app.post("/api/routines/resume-all")
    async def resume_all_routines() -> dict[str, Any]:
        """Resumes user-paused routines; governor pauses stay parked by design."""
        routine_scheduler.init()
        return {"ok": True, "resumed": routine_scheduler.resume_all()}

    @app.get("/api/routines/{rid}")
    async def get_routine(rid: str) -> dict[str, Any]:
        routine_scheduler.init()
        return {"ok": True, "routine": _found(routine_scheduler.get_routine(rid))}

    @app.patch("/api/routines/{rid}")
    async def patch_routine(rid: str, body: RoutinePatchBody) -> dict[str, Any]:
        routine_scheduler.init()
        _found(routine_scheduler.get_routine(rid))
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        return {"ok": True, "routine": routine_scheduler.update_routine(rid, **fields)}

    @app.delete("/api/routines/{rid}")
    async def delete_routine(rid: str) -> dict[str, Any]:
        routine_scheduler.init()
        _found(routine_scheduler.get_routine(rid))
        return {"ok": routine_scheduler.delete_routine(rid)}

    @app.post("/api/routines/{rid}/pause")
    async def pause_routine(rid: str, body: PauseBody | None = None) -> dict[str, Any]:
        routine_scheduler.init()
        _found(routine_scheduler.get_routine(rid))
        reason = body.reason if body else "user"
        return {"ok": True, "routine": routine_scheduler.pause(rid, reason)}

    @app.post("/api/routines/{rid}/resume")
    async def resume_routine(rid: str) -> dict[str, Any]:
        routine_scheduler.init()
        _found(routine_scheduler.get_routine(rid))
        return {"ok": True, "routine": routine_scheduler.resume(rid)}

    @app.post("/api/routines/{rid}/run")
    async def run_routine(rid: str) -> dict[str, Any]:
        routine_scheduler.init()
        _found(routine_scheduler.get_routine(rid))
        return {"ok": True, "run": await routine_scheduler.run_once(rid)}

    @app.post("/api/routines/{rid}/fire")
    async def fire_routine(rid: str, body: FireBody) -> dict[str, Any]:
        """External /fire trigger — wraps payload as untrusted data (Claude Code routines pattern)."""
        from CortexOS.execution.untrusted_payload import prepare_external_prompt

        routine_scheduler.init()
        routine = _found(routine_scheduler.get_routine(rid))
        prepared = prepare_external_prompt(
            str(routine.get("prompt") or ""),
            body.external_text,
            source=body.source,
        )

        # OSR runs on the WRAPPED text — classification never sees raw external
        # text as trusted, and an unwrapped payload is refused outright.
        from CortexOS.execution import osr

        band = osr.classify_external(prepared["prompt"]) if prepared["wrapped"] else None
        if band is not None and not band.get("ok"):
            raise HTTPException(status_code=400, detail=humanize.explain(str(band.get("error"))))

        # Temporarily override prompt for this run via vars channel the scheduler merges.
        run = await routine_scheduler.run_once(
            rid,
            prompt_override=prepared["prompt"],
            extra_vars={
                "untrusted_wrapped": prepared["wrapped"],
                "fire_source": body.source,
                "osr_band": (band or {}).get("band"),
            },
        )
        if band is not None and band.get("schema", {}).get("structured"):
            osr.remember_schema(band["schema"])

        # G2.5 forget-recovery: read commitments out of the payload. The text is
        # still treated as data — this only extracts and stores, never executes.
        commitments_found = None
        if body.external_text:
            from CortexOS.execution import commitments

            commitments_found = commitments.record_from_text(
                body.external_text, source=body.source, source_id=rid
            )
        return {
            "ok": True,
            "wrapped": prepared["wrapped"],
            "osr": band,
            "commitments": commitments_found,
            "run": run,
        }

    @app.get("/api/routines/{rid}/runs")
    async def routine_runs(rid: str) -> dict[str, Any]:
        routine_scheduler.init()
        _found(routine_scheduler.get_routine(rid))
        return {"ok": True, "runs": routine_scheduler.list_runs(rid)}
