"""Workspace + Cursor connector HTTP surface.

No from __future__ import annotations (FastAPI). No packs imports (C2).
"""
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from CortexOS.connectors import cursor_session, workspaces
from CortexOS.connectors.dispatch import dispatch as run_dispatch

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


class DispatchIn(BaseModel):
    text: str = Field(min_length=1)
    kind: str | None = None


class InstructIn(BaseModel):
    instruction: str = Field(min_length=1)


class OpenChatIn(BaseModel):
    workspace: str = Field(min_length=1)
    task: str = Field(min_length=1)


@router.get("/workspaces")
def list_workspaces() -> dict[str, Any]:
    return {"workspaces": workspaces.catalog(), "orchestrator": "cortex"}


@router.post("/dispatch")
def dispatch(req: DispatchIn) -> dict[str, Any]:
    try:
        return run_dispatch(req.text, kind=req.kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/cursor/chats")
def list_chats() -> dict[str, Any]:
    return {"chats": cursor_session.get_port().list_chats()}


@router.post("/cursor/chats")
def open_chat(req: OpenChatIn) -> dict[str, Any]:
    try:
        workspaces.get(req.workspace)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    chat_id = cursor_session.get_port().open_chat(req.workspace, req.task)
    return {"id": chat_id, "workspace": req.workspace, "new_cursor_chat": True}


@router.get("/cursor/chats/{chat_id}/messages")
def get_messages(chat_id: str) -> dict[str, Any]:
    try:
        msgs = cursor_session.get_port().messages(chat_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"chat_id": chat_id, "messages": msgs}


@router.post("/cursor/chats/{chat_id}/instruct")
def instruct(chat_id: str, req: InstructIn) -> dict[str, Any]:
    try:
        chat = cursor_session.get_port().instruct(chat_id, req.instruction)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"chat_id": chat_id, "status": chat.get("status"), "messages": chat.get("messages")}


@router.get("", response_class=HTMLResponse)
def connector_ui() -> str:
    rows = "".join(
        f"<tr><td>{w['id']}</td><td>{w['role']}</td><td><code>{w['root']}</code></td>"
        f"<td>{'yes' if w['present'] else 'no'}</td></tr>"
        for w in workspaces.catalog()
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Cortex connectors</title>
<style>
body {{ font-family: sans-serif; max-width: 52rem; margin: 2rem auto; }}
code {{ font-size: 0.9em; }}
label {{ display: block; margin: 0.6rem 0 0.2rem; }}
</style></head>
<body>
<h1>Cortex connectors</h1>
<p>Orchestrator is Cortex (DAG/routines/seeker). Not LangGraph.
New <b>task</b> opens a new Cursor chat. Normal <b>chat</b> stays on chatbot.</p>
<table border="1" cellpadding="6" cellspacing="0">
<tr><th>id</th><th>role</th><th>root</th><th>present</th></tr>
{rows}
</table>
<form id="f">
<label>kind
<select name="kind"><option value="task">task (new Cursor chat)</option>
<option value="chat">chat (chatbot repo)</option></select></label>
<label>message <input name="text" size="60" required></label>
<button type="submit">Dispatch</button>
</form>
<pre id="out"></pre>
<script>
document.getElementById('f').onsubmit = async (e) => {{
  e.preventDefault();
  const fd = new FormData(e.target);
  const r = await fetch('/api/connectors/dispatch', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{text: fd.get('text'), kind: fd.get('kind')}}),
  }});
  document.getElementById('out').textContent = JSON.stringify(await r.json(), null, 2);
}};
</script>
</body></html>
"""


def register_connector_routes(app: Any) -> None:
    app.include_router(router)
