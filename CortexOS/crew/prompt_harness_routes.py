"""HTTP mount for EPIC-PROMPT-HARNESS-CLIMB. Off FreeRoute handlers in server.py.

No from __future__ import annotations (FastAPI route module rule).
Pydantic models are hoisted to module level.
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from CortexOS.security.auth_port import require_spend_auth

# #265 (P24.4): the POST runs FreeRoute completions, so it needs an engine
# auth-port caller (viewer+), loopback included, before any model call.
_SPEND = [Depends(require_spend_auth("viewer"))]


class PromptHarnessIn(BaseModel):
    corpus: str = ""
    distill: dict[str, Any] | None = None
    claim_complete: bool = False
    gpu_finetune: bool = False
    trained_jepa: bool = False
    claimed_gen: str = ""
    live_5000_ci: bool = False
    live_8020_ci: bool = False
    paste_langgraph: bool = False
    candidates: list[dict[str, Any]] | None = None
    cases: list[dict[str, Any]] | None = Field(default=None)


def mount_prompt_harness(router: APIRouter) -> None:
    """GET displays the law. POST measures. Does not rewrite FreeRoute."""

    @router.get("/prompt-harness")
    async def prompt_harness_map() -> dict[str, Any]:
        """Prompt+harness climb law. No model call. #212 stays OPEN."""
        from CortexOS.crew import prompt_harness_climb as harness

        return harness.public_map()

    @router.post("/prompt-harness", dependencies=_SPEND)
    async def prompt_harness_run(body: PromptHarnessIn | None = None) -> Any:
        """Measure vs DMS #180. Unarmed fail-closed. Never invent COMPLETE."""
        from CortexOS.crew import prompt_harness_climb as harness

        payload = body or PromptHarnessIn()
        return await harness.run_harness(
            cases=payload.cases,
            distill_recipe=payload.distill,
            claim_complete=payload.claim_complete,
            gpu_finetune=payload.gpu_finetune,
            trained_jepa=payload.trained_jepa,
            claimed_gen=payload.claimed_gen,
            live_5000_ci=payload.live_5000_ci,
            live_8020_ci=payload.live_8020_ci,
            paste_langgraph=payload.paste_langgraph,
            corpus=payload.corpus,
            candidates=payload.candidates,
        )
