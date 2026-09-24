"""Workspace catalog, Cursor session, agent inbox, and computer-control probe.

GET /api/connectors is the Constructor-style operator desk: sidebar of
specialized agents, chat pane, computer-control status. Same engine as
POST /api/connectors/dispatch -- Cortex is the orchestrator; Cursor is a
worker. Computer control is fail-closed (probe by default; no mouse/keyboard
on this host unless a Windows sidecar is armed).

No from __future__ import annotations (FastAPI). No packs imports (C2).
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from CortexOS.api.app_routes import scrub_host_paths
from CortexOS.connectors import agents, computer_control, cursor_session, workspaces
from CortexOS.connectors.dispatch import dispatch as run_dispatch
from CortexOS.security.auth_port import require_role

# TRUST-01 (#257): the whole router needs at least viewer, through the engine's
# auth port (no packs import). Routes that dispatch work need steward, and
# driving the host's mouse and keyboard needs admin.
router = APIRouter(
    prefix="/api/connectors",
    tags=["connectors"],
    dependencies=[Depends(require_role("viewer"))],
)
_STEWARD = [Depends(require_role("steward"))]
_ADMIN = [Depends(require_role("admin"))]


# T2-RESP (#263): a workspace's host directory never leaves the engine, even for
# an authorized viewer. The catalog keeps ``present`` and ``env`` (which variable
# to set), so an operator can still see and fix a missing workspace.
_WORKSPACE_PATH_KEYS = frozenset({"root", "windows_default", "workspace_root"})


def _workspace_roots() -> list[str]:
    try:
        return [str(w.get("root") or "") for w in workspaces.catalog()]
    except Exception:  # noqa: BLE001 - redaction still covers the engine's own roots
        return []


def _public(value: Any, *, error_text: bool = False) -> Any:
    """Engine-generated connector payloads without host paths.

    Message and chat text is what a caller typed, so it is returned as written;
    only the engine-built fields (workspace rows, dispatch results, the probe,
    error details) are redacted here.
    """
    return scrub_host_paths(
        value,
        drop_keys=_WORKSPACE_PATH_KEYS,
        extra_roots=_workspace_roots(),
        error_text=error_text,
    )


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=_public(str(exc), error_text=True))


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=_public(str(exc), error_text=True))


class DispatchIn(BaseModel):
    text: str = Field(min_length=1)
    kind: str | None = None
    workspace: str | None = None


class InstructIn(BaseModel):
    instruction: str = Field(min_length=1)


class OpenChatIn(BaseModel):
    workspace: str = Field(min_length=1)
    task: str = Field(min_length=1)


class AgentPostIn(BaseModel):
    text: str = Field(min_length=1)
    kind: str | None = None


class ComputerControlIn(BaseModel):
    action: str = Field(min_length=1)
    x: int | None = None
    y: int | None = None
    text: str | None = None


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_UI_CSS = """
:root { --bg:#0e1117; --side:#161b22; --card:#1c2128; --line:#30363d; --txt:#e6edf3; --dim:#8b949e; --acc:#388bfd; --sel:#1f6feb33; }
* { box-sizing: border-box; }
html, body { margin:0; height:100%; background:var(--bg); color:var(--txt); font:14px/1.4 -apple-system,Segoe UI,sans-serif; }
.app { display:flex; height:100vh; }
.side { width:280px; background:var(--side); border-right:1px solid var(--line); display:flex; flex-direction:column; }
.search { margin:12px; padding:8px 10px; border-radius:6px; border:1px solid var(--line); background:#0d1117; color:var(--txt); }
.agents { flex:1; overflow:auto; }
.agent { display:flex; gap:10px; padding:10px 12px; cursor:pointer; border-left:3px solid transparent; text-decoration:none; color:inherit; }
.agent:hover { background:#21262d; }
.agent.on { background:var(--sel); border-left-color:var(--acc); }
.ic { width:32px; height:32px; border-radius:8px; display:flex; align-items:center; justify-content:center; font-size:16px; flex-shrink:0; }
.nm { font-weight:600; }
.sn { color:var(--dim); font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.foot { padding:10px 12px; border-top:1px solid var(--line); font-size:12px; color:var(--dim); }
.main { flex:1; display:flex; flex-direction:column; min-width:0; }
.head { padding:14px 18px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; align-items:center; }
.chip { font-size:11px; padding:2px 8px; border-radius:999px; border:1px solid var(--line); color:var(--dim); }
.chip.ok { border-color:#238636; color:#3fb950; }
.chip.off { border-color:#da3633; color:#f85149; }
.pane { flex:1; overflow:auto; padding:18px; }
.msg { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; margin:8px 0; }
.who { font-size:12px; color:var(--dim); margin-bottom:4px; }
.composer { padding:12px 18px; border-top:1px solid var(--line); }
.composer form { display:flex; gap:8px; }
.composer input[type=text] { flex:1; padding:10px 12px; border-radius:8px; border:1px solid var(--line); background:#0d1117; color:var(--txt); }
.composer button { background:var(--acc); color:#fff; border:0; border-radius:8px; padding:10px 16px; cursor:pointer; }
code { font-size:12px; }
"""


def _ui_html() -> str:
    import json

    roster = agents.roster()
    cc = computer_control.probe()
    chip = "ok" if cc.get("uacc_importable") else "off"
    chip_label = "UACC importable" if cc.get("uacc_importable") else "computer control off"
    rows = []
    for a in roster:
        href = f"/api/connectors?agent={_html_escape(a['id'])}"
        rows.append(
            f'<a class="agent" data-id="{_html_escape(a["id"])}" href="{href}">'
            f'<div class="ic" style="background:{_html_escape(str(a.get("color") or "#388bfd"))}22">'
            f'{_html_escape(str(a.get("icon") or a["id"][:1].upper()))}</div>'
            f'<div><div class="nm">{_html_escape(a["name"])}</div>'
            f'<div class="sn">{_html_escape(str(a.get("snippet") or a.get("blurb") or ""))}</div></div></a>'
        )
    agent_list = "\n".join(rows)
    agents_json = json.dumps(roster)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Constructor Agent</title>
<style>{_UI_CSS}</style></head>
<body><div class="app">
<aside class="side">
  <input class="search" id="search" placeholder="Search" aria-label="Search agents">
  <div class="agents" id="agentlist">{agent_list}</div>
  <div class="foot">Plugins · Cortex operator desk · Not LangGraph</div>
</aside>
<main class="main">
  <header class="head">
    <div><strong id="title">Constructor Agent</strong>
      <div class="sn" id="sub">Cortex orchestrates. Cursor is a worker. Computer control is fail-closed.</div>
    </div>
    <span class="chip {chip}" id="ccchip">{_html_escape(chip_label)}</span>
  </header>
  <div class="pane" id="pane">
    <p class="sn">NEW</p>
    <div class="msg"><div class="who">system</div>
      New <b>task</b> opens a Cursor chat. <b>chat</b> stays on chatbot. Not LangGraph.
      Computer control is off until a Windows sidecar is armed.</div>
  </div>
  <div class="composer">
    <form id="f">
      <input type="hidden" id="agent_id" value="constructor">
      <input id="text" type="text" placeholder="Message Constructor Agent" autocomplete="off">
      <button type="submit">Send</button>
    </form>
  </div>
</main>
</div>
<script>
const agents = {agents_json};
const pane = document.getElementById('pane');
const title = document.getElementById('title');
const sub = document.getElementById('sub');
const ph = document.getElementById('text');
const aid = document.getElementById('agent_id');
function pick(id) {{
  const a = agents.find(x => x.id === id) || agents[0];
  aid.value = a.id;
  title.textContent = a.name;
  sub.textContent = a.role || a.blurb || '';
  ph.placeholder = 'Message ' + a.name;
  document.querySelectorAll('.agent').forEach(el => el.classList.toggle('on', el.dataset.id === a.id));
  load(a.id);
}}
async function load(id) {{
  const r = await fetch('/api/connectors/agents/' + id + '/messages');
  const j = await r.json();
  pane.innerHTML = '<p class="sn">NEW</p>' + ((j.messages||[]).length
    ? (j.messages||[]).map(m =>
        '<div class="msg"><div class="who">'+m.role+' · '+(m.ts||'')+'</div>'+escapeHtml(m.text||'')+'</div>'
      ).join('')
    : '<div class="msg"><div class="who">system</div>No messages yet.</div>');
  pane.scrollTop = pane.scrollHeight;
}}
function escapeHtml(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}
document.querySelectorAll('.agent').forEach(el => el.addEventListener('click', e => {{
  e.preventDefault(); pick(el.dataset.id);
}}));
document.getElementById('search').addEventListener('input', e => {{
  const q = e.target.value.toLowerCase();
  document.querySelectorAll('.agent').forEach(el => {{
    el.style.display = el.textContent.toLowerCase().includes(q) ? '' : 'none';
  }});
}});
document.getElementById('f').addEventListener('submit', async e => {{
  e.preventDefault();
  const text = ph.value.trim();
  if (!text) return;
  const r = await fetch('/api/connectors/agents/' + aid.value + '/messages', {{
    method: 'POST', headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{text: text, kind: 'task'}})
  }});
  ph.value = '';
  await load(aid.value);
  if (!r.ok) {{
    const err = await r.json().catch(() => ({{}}));
    pane.innerHTML += '<div class="msg"><div class="who">error</div>'+escapeHtml(JSON.stringify(err))+'</div>';
  }}
}});
pick('constructor');
</script>
</body></html>"""


@router.get("/workspaces")
def list_workspaces() -> dict[str, Any]:
    return dict(_public({"workspaces": workspaces.catalog(), "orchestrator": "cortex"}))


@router.get("/agents")
def list_agents() -> dict[str, Any]:
    return {"agents": agents.roster()}


@router.get("/agents/{agent_id}/messages")
def get_agent_messages(agent_id: str) -> dict[str, Any]:
    try:
        agent = agents.get(agent_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    return {"agent": agent, "messages": agents.messages(agent_id)}


@router.post("/agents/{agent_id}/messages", dependencies=_STEWARD)
def post_agent_message(agent_id: str, body: AgentPostIn) -> dict[str, Any]:
    try:
        out = agents.post(agent_id, body.text, kind=body.kind)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    out = dict(out)
    out["dispatch"] = _public(out.get("dispatch"))
    return out


@router.get("/computer-control")
def computer_control_status() -> dict[str, Any]:
    return dict(_public(computer_control.probe()))


@router.post("/computer-control/invoke", dependencies=_ADMIN)
def computer_control_invoke(body: ComputerControlIn) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if body.x is not None:
        kwargs["x"] = body.x
    if body.y is not None:
        kwargs["y"] = body.y
    if body.text is not None:
        kwargs["text"] = body.text
    out = computer_control.invoke(body.action, **kwargs)
    if not out.get("ok"):
        raise HTTPException(status_code=403, detail=_public(out))
    return dict(_public(out))


@router.post("/dispatch", dependencies=_STEWARD)
def dispatch(req: DispatchIn) -> dict[str, Any]:
    try:
        out = run_dispatch(req.text, kind=req.kind, workspace=req.workspace)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except KeyError as exc:
        raise _not_found(exc) from exc
    return dict(_public(out))


@router.get("/cursor/chats")
def list_chats() -> dict[str, Any]:
    return {"chats": cursor_session.get_port().list_chats()}


@router.post("/cursor/chats", dependencies=_STEWARD)
def open_chat(req: OpenChatIn) -> dict[str, Any]:
    try:
        workspaces.get(req.workspace)
    except KeyError as exc:
        raise _not_found(exc) from exc
    chat_id = cursor_session.get_port().open_chat(req.workspace, req.task)
    return {"id": chat_id, "workspace": req.workspace, "new_cursor_chat": True}


@router.get("/cursor/chats/{chat_id}/messages")
def get_messages(chat_id: str) -> dict[str, Any]:
    try:
        msgs = cursor_session.get_port().messages(chat_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    return {"chat_id": chat_id, "messages": msgs}


@router.post("/cursor/chats/{chat_id}/instruct", dependencies=_STEWARD)
def instruct(chat_id: str, req: InstructIn) -> dict[str, Any]:
    try:
        chat = cursor_session.get_port().instruct(chat_id, req.instruction)
    except KeyError as exc:
        raise _not_found(exc) from exc
    return {"chat_id": chat_id, "status": chat.get("status"), "messages": chat.get("messages")}


@router.get("", response_class=HTMLResponse)
def connector_ui() -> str:
    return _ui_html()


def register_connector_routes(app: Any) -> None:
    app.include_router(router)
