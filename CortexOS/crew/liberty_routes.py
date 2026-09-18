"""HTTP mount for LIBERTY-SEEK + proxy JEPA collapse. Kept off FreeRoute handlers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from CortexOS.execution import seeker


class LibertySeekIn(BaseModel):
    goal_id: str | None = None
    statement: str = ""
    trigger: str = "liberty"
    execute: bool = False
    limit: int = seeker.MAX_PROPOSALS


def mount_liberty(router: APIRouter) -> None:
    """Crew POST starts seek. Control GET-displays. No FreeRoute rewrite."""

    @router.get("/liberty")
    async def liberty_map() -> dict[str, Any]:
        """G2.1 seek law. Control may GET-display. Does not seek."""
        from CortexOS.crew import liberty_seek as liberty_seek_mod

        return liberty_seek_mod.control_stamp()

    @router.post("/liberty/seek")
    async def liberty_seek(body: LibertySeekIn | None = None) -> Any:
        """Operator start. Runs G2.1 seeker. execute=true parks. No invent autonomy."""
        from CortexOS.crew import liberty_seek as liberty_seek_mod

        payload = body or LibertySeekIn()
        return liberty_seek_mod.start_seek(
            goal_id=payload.goal_id,
            statement=payload.statement,
            trigger=payload.trigger,
            execute=payload.execute,
            limit=payload.limit,
        )

    @router.post("/liberty")
    async def liberty_post_refused() -> Any:
        raise HTTPException(
            405,
            "Liberty start is POST /crew/liberty/seek. Control does not POST spawn.",
        )
