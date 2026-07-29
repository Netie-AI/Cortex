"""HTTP surface for the bound enterprise goal and the proactive seeker.

`POST /api/engine/seek` is the active engine's front door: no ingress, no
event, no prompt — just "what moves the goal next?". Every response carries its
assumptions in words, the same law the routine drafts follow.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from CortexOS.execution import enterprise_goal, humanize, seeker


class GoalBody(BaseModel):
    statement: str = ""
    org_id: str = ""
    measurable_criteria: list[dict[str, Any]] = Field(default_factory=list)
    hard_constraints: list[dict[str, Any]] = Field(default_factory=list)
    soft_preferences: dict[str, Any] = Field(default_factory=dict)
    audit_required: bool = True


class GoalPatchBody(BaseModel):
    statement: str | None = None
    measurable_criteria: list[dict[str, Any]] | None = None
    hard_constraints: list[dict[str, Any]] | None = None
    soft_preferences: dict[str, Any] | None = None
    audit_required: bool | None = None
    active: bool | None = None


class SeekBody(BaseModel):
    goal_id: str | None = None
    trigger: str = "manual"
    limit: int = seeker.MAX_PROPOSALS


class ScanBody(BaseModel):
    text: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)  # provenance is required, not optional
    source_id: str = ""


class OsrBody(BaseModel):
    text: str = Field(..., min_length=1)
    wrapped: bool = False  # true when the caller already wrapped untrusted ingress


class OutcomeBody(BaseModel):
    proposal_id: str = Field(..., min_length=1)
    outcome: str = Field(..., min_length=1)  # accepted | succeeded | dismissed | failed


def _found(goal: dict[str, Any] | None) -> dict[str, Any]:
    if goal is None:
        raise HTTPException(status_code=404, detail=humanize.explain("unknown_goal"))
    return goal


def register_goal_routes(app: Any) -> None:
    @app.get("/api/goals")
    async def list_goals() -> dict[str, Any]:
        return {"ok": True, "goals": enterprise_goal.list_goals()}

    @app.post("/api/goals")
    async def create_goal(body: GoalBody) -> dict[str, Any]:
        out = enterprise_goal.create_goal(
            body.statement,
            org_id=body.org_id,
            measurable_criteria=body.measurable_criteria,
            hard_constraints=body.hard_constraints,
            soft_preferences=body.soft_preferences,
            audit_required=body.audit_required,
        )
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=humanize.explain(str(out.get("error"))))
        return out

    @app.get("/api/goals/{goal_id}")
    async def get_goal(goal_id: str) -> dict[str, Any]:
        return {"ok": True, "goal": _found(enterprise_goal.get_goal(goal_id))}

    @app.patch("/api/goals/{goal_id}")
    async def patch_goal(goal_id: str, body: GoalPatchBody) -> dict[str, Any]:
        _found(enterprise_goal.get_goal(goal_id))
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        return {"ok": True, "goal": enterprise_goal.update_goal(goal_id, **fields)}

    @app.delete("/api/goals/{goal_id}")
    async def delete_goal(goal_id: str) -> dict[str, Any]:
        _found(enterprise_goal.get_goal(goal_id))
        return {"ok": enterprise_goal.delete_goal(goal_id)}

    @app.get("/api/goals/{goal_id}/seeks")
    async def goal_seeks(goal_id: str) -> dict[str, Any]:
        _found(enterprise_goal.get_goal(goal_id))
        return {"ok": True, "seeks": enterprise_goal.list_seeks(goal_id)}

    @app.post("/api/goals/{goal_id}/outcome")
    async def proposal_outcome(goal_id: str, body: OutcomeBody) -> dict[str, Any]:
        """What the user did with a proposal — this is what the ranking learns from."""
        _found(enterprise_goal.get_goal(goal_id))
        out = seeker.record_proposal_outcome(goal_id, body.proposal_id, body.outcome)
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=humanize.explain(str(out.get("error"))))
        return out

    @app.get("/api/commitments")
    async def list_commitments(status: str = "open", limit: int = 50) -> dict[str, Any]:
        from CortexOS.execution import commitments

        return {"ok": True, "commitments": commitments.list_commitments(status, limit)}

    @app.post("/api/commitments/scan")
    async def scan_commitments(body: ScanBody) -> dict[str, Any]:
        """Find commitments in text. Extract-and-store only — never executes it."""
        from CortexOS.execution import commitments

        out = commitments.record_from_text(
            body.text, source=body.source, source_id=body.source_id
        )
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=humanize.explain(str(out.get("error"))))
        return out

    @app.post("/api/commitments/{cid}/close")
    async def close_commitment(cid: str) -> dict[str, Any]:
        from CortexOS.execution import commitments

        record = commitments.close(cid)
        if record is None:
            raise HTTPException(status_code=404, detail=humanize.explain("unknown_commitment"))
        return {"ok": True, "commitment": record}

    @app.post("/api/commitments/{cid}/dismiss")
    async def dismiss_commitment(cid: str) -> dict[str, Any]:
        from CortexOS.execution import commitments

        record = commitments.dismiss(cid)
        if record is None:
            raise HTTPException(status_code=404, detail=humanize.explain("unknown_commitment"))
        return {"ok": True, "commitment": record}

    @app.get("/api/engine/telemetry")
    async def engine_telemetry(goal_id: str = "", limit: int = 25) -> dict[str, Any]:
        """Run history as numbers — safe to display, contains no user content."""
        from CortexOS.execution import action_event, action_value

        family = ""
        if goal_id:
            goal = _found(enterprise_goal.get_goal(goal_id))
            family = action_value.goal_family(goal)
        return {
            "ok": True,
            "summary": action_event.summary(family or None),
            "events": action_event.list_events(limit=limit, goal_family=family or None),
            "daily": action_event.daily(family or None),
        }

    @app.post("/api/engine/telemetry/compact")
    async def engine_telemetry_compact() -> dict[str, Any]:
        from CortexOS.execution import action_event

        return action_event.compact()

    @app.get("/api/goals/{goal_id}/values")
    async def goal_values(goal_id: str) -> dict[str, Any]:
        from CortexOS.execution import action_value

        goal = _found(enterprise_goal.get_goal(goal_id))
        return {"ok": True, "values": action_value.table(action_value.goal_family(goal))}

    @app.post("/api/engine/osr")
    async def engine_osr(body: OsrBody) -> dict[str, Any]:
        """Classify-only: which band does this work fall into, and why?"""
        from CortexOS.execution import osr

        if body.wrapped:
            out = osr.classify_external(body.text)
            if not out.get("ok"):
                raise HTTPException(
                    status_code=400, detail=humanize.explain(str(out.get("error")))
                )
            return out
        return {"ok": True, **osr.classify(body.text)}

    @app.post("/api/engine/seek")
    async def engine_seek(body: SeekBody | None = None) -> dict[str, Any]:
        payload = body or SeekBody()
        out = seeker.seek(payload.goal_id, trigger=payload.trigger, limit=payload.limit)
        if not out.get("ok"):
            raise HTTPException(status_code=404, detail=humanize.explain(str(out.get("error"))))
        return out
