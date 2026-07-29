"""In-process A2A message endpoint."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from CortexOS.a2a.runtime import send_message
from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/a2a", tags=["a2a"])


class A2AMessageRequest(BaseModel):
    to: str
    body: Dict[str, Any]
    from_did: Optional[str] = None
    type: Optional[str] = None


@router.post("/messages")
def post_message(
    request: A2AMessageRequest,
    caller: Caller = Depends(require_role("viewer")),
) -> Any:
    _ = caller
    reply = send_message(
        to=request.to,
        body=request.body,
        **({"from_did": request.from_did} if request.from_did is not None else {}),
        **({"type": request.type} if request.type is not None else {}),
    )
    if reply is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "unknown_agent"},
        )
    return {"ok": True, "reply": reply.model_dump(mode="json")}


def register_a2a_routes(app: Any) -> None:
    app.include_router(router)
