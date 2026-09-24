"""F2 governed chat API route registration."""

from typing import Any

from fastapi import Depends
from pydantic import BaseModel, Field

from CortexOS.security.auth_port import Principal, require_role


class CreateThreadRequest(BaseModel):
    external_ref: str | None = None
    customer_label: str | None = None
    actor: str = "demo_operator"  # ignored: the caller is recorded (T2-DMS, #263)
    tenant_id: str = "default"


class AppendMessageRequest(BaseModel):
    sender: str
    body: str
    direction: str = Field(default="inbound", description="inbound | outbound")
    lang: str | None = None
    actor: str = "demo_operator"  # ignored: the caller is recorded (T2-DMS, #263)


# T2-DMS (#263): gated through the engine's auth port. Reading a thread needs
# viewer; opening a thread or appending to one needs steward, and the ledger
# actor is the authenticated caller, never a name from the request body.
_VIEWER = [Depends(require_role("viewer"))]
_STEWARD = require_role("steward")


def register_chat_routes(app: Any) -> None:
    from fastapi import HTTPException

    from packs.dms.chat import threads as chat_threads

    @app.post("/dms/threads")
    async def dms_create_thread(
        body: CreateThreadRequest, caller: Principal = Depends(_STEWARD)
    ) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        return chat_threads.create_thread(
            external_ref=body.external_ref,
            customer_label=body.customer_label,
            actor=caller.actor,
            tenant_id=body.tenant_id,
        )

    @app.post("/dms/threads/{thread_id}/messages")
    async def dms_append_message(
        thread_id: str, body: AppendMessageRequest, caller: Principal = Depends(_STEWARD)
    ) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        try:
            return chat_threads.append_message(
                thread_id=thread_id,
                sender=body.sender,
                body=body.body,
                direction=body.direction,
                lang=body.lang,
                actor=caller.actor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/dms/threads/{thread_id}/messages", dependencies=_VIEWER)
    async def dms_list_messages(thread_id: str) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        try:
            messages = chat_threads.list_messages(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"thread_id": thread_id, "messages": messages}
