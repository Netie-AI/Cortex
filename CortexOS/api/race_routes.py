"""HTTP surface for the racing router + architecture scoreboard.

AirGPT proxies these under the same paths on :8765. POST /api/engine/auto is
the one entry point agents need: the family gate decides direct-vs-race,
the scoreboard learns from every run.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from CortexOS.execution import race_router, scoreboard


class AutoRouteBody(BaseModel):
    goal: str = ""
    prompt: str = ""  # AirGPT alias
    predicates: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    min_runs: int = 3
    sim_threshold: float = 0.80
    scale: bool = True


def register_race_routes(app: Any) -> None:
    @app.post("/api/engine/auto")
    async def engine_auto(body: AutoRouteBody) -> dict[str, Any]:
        goal = (body.goal or body.prompt).strip()
        if not goal:
            raise HTTPException(status_code=400, detail="goal or prompt required")
        run_body: dict[str, Any] = {"prompt": goal, "params": body.params}
        if body.session_id:
            run_body["session_id"] = body.session_id
        return await race_router.auto_route(
            goal,
            run_body,
            predicates=body.predicates or None,
            min_runs=body.min_runs,
            sim_threshold=body.sim_threshold,
            scale=body.scale,
        )

    @app.get("/api/engine/scoreboard")
    async def scoreboard_families() -> dict[str, Any]:
        scoreboard.init()
        return {"ok": True, "families": scoreboard.list_families()}

    @app.get("/api/engine/scoreboard/{family}")
    async def scoreboard_family(family: str) -> dict[str, Any]:
        scoreboard.init()
        return {
            "ok": True,
            "family": family,
            "stats": scoreboard.family_stats(family),
            "best": scoreboard.best_preset(family),
        }
