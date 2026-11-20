"""F2 governed chat API route registration."""

from typing import Any

from pydantic import BaseModel, Field


# #263: ``actor`` is the authenticated caller. The field stays on the wire
# unchanged (frozen contract spec components) and its value is never read.
class CreateThreadRequest(BaseModel):
    external_ref: str | None = None
    customer_label: str | None = None
    actor: str = "demo_operator"  # wire only (#263): ignored; the actor is the authenticated caller
    tenant_id: str = "default"


class AppendMessageRequest(BaseModel):
    sender: str
    body: str
    direction: str = Field(default="inbound", description="inbound | outbound")
    lang: str | None = None
    actor: str = "demo_operator"  # wire only (#263): ignored; the actor is the authenticated caller


def register_chat_routes(app: Any) -> None:
    from fastapi import Depends, HTTPException

    from CortexOS.security.auth_port import Principal, require_role
    from packs.dms.chat import threads as chat_threads

    @app.post("/dms/threads")
    async def dms_create_thread(
        body: CreateThreadRequest, caller: Principal = Depends(require_role("steward"))
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
        thread_id: str,
        body: AppendMessageRequest,
        caller: Principal = Depends(require_role("steward")),
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

    @app.get("/dms/threads/{thread_id}/messages", dependencies=[Depends(require_role("viewer"))])
    async def dms_list_messages(thread_id: str) -> dict[str, Any]:
        pack = getattr(app.state, "pack", None)
        if pack is None or pack.name != "dms":
            raise HTTPException(status_code=404, detail="DMS routes require PACK=dms")
        try:
            messages = chat_threads.list_messages(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"thread_id": thread_id, "messages": messages}
