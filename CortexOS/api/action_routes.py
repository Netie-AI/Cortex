"""F8 sandboxed action routes — POST /dms/actions/{tool} (steward+)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/dms/actions", tags=["actions"])


class ActionRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None


@router.get("")
def list_actions(caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from netie.execution.tool_runner import allowed_action_tools

    _ = caller
    return {"tools": sorted(allowed_action_tools())}


@router.get("/{tool}")
def describe_action(
    tool: str,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    from netie.execution.tool_runner import allowed_action_tools

    _ = caller
    return {"tool": tool, "allowed": tool in allowed_action_tools()}


@router.post("/{tool}")
def invoke_action(
    tool: str,
    req: ActionRequest,
    caller: Caller = Depends(require_role("steward")),
) -> dict[str, Any]:
    from netie.execution.tool_runner import ToolCallError, run_tool_call

    try:
        return run_tool_call(
            tool,
            req.params,
            actor=caller.actor,
            run_id=req.run_id,
        )
    except ToolCallError as exc:
        status = 403 if exc.verdict in ("allowlist", "path_escape") else 400
        raise HTTPException(
            status_code=status,
            detail={"error": str(exc), "verdict": exc.verdict, **exc.detail},
        ) from exc


def register_action_routes(app) -> None:
    app.include_router(router)
