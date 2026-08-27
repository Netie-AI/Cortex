"""Constructor skin + OpenVault-gated compile/fetch/run on /cortex/*."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from packs.dms.constructor_fetch import fetch_slice
from packs.dms.constructor_graph import compile_ir, ghost_walk, recommend
from packs.dms.security.api_auth import Caller, resolve_caller

COOKIE = "cortex_session"
_OFF_SCHEMA = {"include_in_schema": False}


def skin_dir() -> Path | None:
    env = (os.environ.get("CONSTRUCTOR_SKIN_DIR") or "").strip()
    candidates = []
    if env:
        candidates.append(Path(env))
    sibling = Path(__file__).resolve().parents[2].parent / "Constructor"
    candidates.append(sibling)
    for path in candidates:
        if path.is_dir() and (path / "index.html").is_file():
            return path
    return None


def _json_body(request_body: Any) -> dict[str, Any]:
    return request_body if isinstance(request_body, dict) else {}


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost", "testclient")


def _extract_key(request: Request, body_key: str | None = None) -> str:
    if body_key and body_key.strip():
        return body_key.strip()
    header = (request.headers.get("X-API-Key") or "").strip()
    if header:
        return header
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.cookies.get(COOKIE) or "").strip()


def _refuse_demo(key: str) -> bool:
    flag = os.environ.get("DMS_REFUSE_DEMO_KEYS", "").strip().lower()
    return flag in ("1", "true", "yes") and key.startswith("dms-demo-")


def resolve_constructor_caller(request: Request, body_key: str | None = None) -> Caller | None:
    key = _extract_key(request, body_key)
    if not key or _refuse_demo(key):
        return None
    caller = resolve_caller(key)
    if caller is not None:
        return caller
    if key.startswith("ov_"):
        from CortexOS.integrations.openvault_keys import verify_token

        verified = verify_token(key)
        if verified:
            kid = str((verified.get("key") or {}).get("key_id") or "ov")
            return Caller(role="steward", actor=f"ov_{kid}")
    return None


def _need_caller(request: Request) -> Caller | JSONResponse:
    caller = resolve_constructor_caller(request)
    if caller is None:
        return JSONResponse(
            {"ok": False, "error": "Valid API key required (X-API-Key, Bearer, or session cookie)"},
            status_code=401,
        )
    return caller


def _login_html() -> str:
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Cortex login</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
body{font:16px/1.4 system-ui,sans-serif;margin:2rem;max-width:28rem;color:#111}
input,button{font:inherit;padding:.4rem .6rem;width:100%;box-sizing:border-box;margin:.3rem 0}
.hint{color:#555;font-size:.9rem}
</style></head><body>
<h1>Cortex</h1>
<p class="hint">Paste an OpenVault ov_ key or issue one on loopback. Session cookie is set here.</p>
<input id="key" type="password" autocomplete="off" placeholder="ov_..."/>
<button type="button" id="bind">Bind session</button>
<button type="button" id="issue">Issue ov_ key</button>
<p id="msg" class="hint"></p>
<script>
async function post(path, body){
  const res = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
    credentials:'same-origin', body: JSON.stringify(body||{})});
  const text = await res.text();
  try { return {ok: res.ok, status: res.status, data: text ? JSON.parse(text) : {}}; }
  catch { return {ok:false, status:res.status, data:{detail:text.slice(0,200)}}; }
}
document.getElementById('bind').onclick = async function(){
  const key = document.getElementById('key').value.trim();
  const out = await post('/cortex/session', {key:key});
  document.getElementById('msg').textContent = out.ok ? 'Bound. Opening Constructor.' :
    ('Refused ' + (out.data.detail || out.data.error || out.status));
  if (out.ok) location.href = '/cortex/constructor/';
};
document.getElementById('issue').onclick = async function(){
  const out = await post('/cortex/constructor/issue-key', {});
  if (out.data && out.data.token){
    document.getElementById('key').value = out.data.token;
    document.getElementById('msg').textContent = 'Issued once. Bind session next.';
    return;
  }
  document.getElementById('msg').textContent = 'Issue failed: ' +
    ((out.data && (out.data.detail || out.data.error)) || out.status);
};
</script></body></html>
"""


def _ontology_catalog() -> dict[str, Any]:
    from CortexOS.dms.warehouse_db import KNOWN_TABLES
    from packs.dms.ontology.registry import load_action_types, load_object_types

    objects: dict[str, Any] = {}
    for ot in load_object_types():
        objects[ot.id] = {
            "points": {p.name: p.type for p in ot.properties if p.agent_visible}
        }
    actions = [
        a.id
        for a in load_action_types()
        if a.kind == "tool" or a.id in ("item.intake", "agent.checked")
    ]
    return {
        "ok": True,
        "objects": objects,
        "actions": actions,
        "fetch_places": [f"warehouse.{t}" for t in KNOWN_TABLES],
    }


def _run_graph(body: dict[str, Any], caller: Caller) -> dict[str, Any]:
    ir = compile_ir(body, ghost=False)
    if not ir.get("ok"):
        return ir
    fetches: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    for node in ir["nodes"]:
        if node.get("fetch_from") or node.get("constructor_kind") in ("connector", "ontology"):
            slice_ = fetch_slice(node)
            fetches[node["id"]] = slice_
            outputs[node["id"]] = {"output": slice_, "tier": node.get("tier") or "T0"}
        else:
            outputs[node["id"]] = {
                "output": {"note": node.get("note"), "kind": node.get("kind")},
                "tier": node.get("tier") or "T0",
            }
    wrote = None
    action_nodes = [
        n
        for n in ir["nodes"]
        if n.get("constructor_kind") in ("tool_call", "app", "foundry")
        and (n.get("action_type") or "export_pptx") == "export_pptx"
    ]
    if action_nodes:
        from packs.dms.actions.export_pptx import write_export_pptx

        run_id = uuid4().hex[:10]
        out_path = Path("outputs") / caller.actor / run_id / "export.pptx"
        title = "Constructor run"
        body_txt = f"actor={caller.actor} fetches={len(fetches)}"
        wrote = str(write_export_pptx(out_path, title=title, body=body_txt))
        outputs[action_nodes[-1]["id"]] = {
            "output": {"path": wrote, "tool": "export_pptx"},
            "tier": action_nodes[-1].get("tier") or "T0",
        }
    return {
        "ok": True,
        "actor": caller.actor,
        "role": caller.role,
        "output_node_id": ir.get("output_node_id"),
        "ir": ir,
        "fetches": fetches,
        "nodes": outputs,
        "wrote": wrote,
    }


def register_constructor_routes(app: Any) -> None:
    def _cortex_home() -> RedirectResponse:
        target = "/cortex/constructor/" if skin_dir() else "/cortex/login"
        return RedirectResponse(target, status_code=307)

    @app.get("/cortex", **_OFF_SCHEMA)
    async def cortex_root() -> RedirectResponse:
        return _cortex_home()

    @app.get("/cortex/", **_OFF_SCHEMA)
    async def cortex_root_slash() -> RedirectResponse:
        return _cortex_home()

    @app.get("/cortex/login", **_OFF_SCHEMA)
    async def cortex_login() -> HTMLResponse:
        return HTMLResponse(_login_html())

    @app.post("/cortex/session", **_OFF_SCHEMA)
    async def cortex_session(request: Request) -> JSONResponse:
        payload = _json_body(await request.json())
        key = str(payload.get("key") or "")
        caller = resolve_constructor_caller(request, key)
        if caller is None:
            return JSONResponse({"ok": False, "error": "key refused"}, status_code=401)
        response = JSONResponse({"ok": True, "actor": caller.actor, "role": caller.role})
        response.set_cookie(
            COOKIE,
            _extract_key(request, key),
            httponly=True,
            samesite="lax",
            path="/",
            max_age=8 * 3600,
        )
        return response

    @app.get("/cortex/constructor/ontology", **_OFF_SCHEMA)
    async def constructor_ontology() -> dict[str, Any]:
        return _ontology_catalog()

    @app.post("/cortex/constructor/ghost", **_OFF_SCHEMA)
    async def constructor_ghost(request: Request) -> dict[str, Any]:
        return ghost_walk(_json_body(await request.json()))

    @app.post("/cortex/constructor/recommend", **_OFF_SCHEMA)
    async def constructor_recommend(request: Request) -> dict[str, Any]:
        return recommend(_json_body(await request.json()))

    @app.post("/cortex/constructor/issue-key", **_OFF_SCHEMA)
    async def constructor_issue_key(request: Request) -> JSONResponse:
        if not _is_loopback(request):
            return JSONResponse(
                {"ok": False, "error": "issue-key is loopback only"},
                status_code=403,
            )
        from CortexOS.integrations.openvault_keys import issue_token

        remote = issue_token(label="constructor", tier="free")
        if not remote or not remote.get("token"):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "OpenVault did not issue a key",
                    "detail": (remote or {}).get("detail") or "offline",
                },
                status_code=503,
            )
        return JSONResponse(remote)

    @app.post("/cortex/constructor/fetch", **_OFF_SCHEMA)
    async def constructor_fetch(request: Request) -> JSONResponse:
        caller = _need_caller(request)
        if isinstance(caller, JSONResponse):
            return caller
        body = _json_body(await request.json())
        nodes = [n for n in (body.get("nodes") or []) if isinstance(n, dict)]
        if not nodes:
            return JSONResponse({"ok": False, "error": "nodes required"}, status_code=400)
        slice_ = fetch_slice(nodes[0])
        return JSONResponse(
            {"ok": True, "actor": caller.actor, "slice": slice_, "fetches": {str(nodes[0].get("id") or "n"): slice_}}
        )

    @app.post("/cortex/constructor/run", **_OFF_SCHEMA)
    async def constructor_run(request: Request) -> JSONResponse:
        caller = _need_caller(request)
        if isinstance(caller, JSONResponse):
            return caller
        return JSONResponse(_run_graph(_json_body(await request.json()), caller))

    directory = skin_dir()
    if directory is not None:
        from fastapi.staticfiles import StaticFiles

        app.mount(
            "/cortex/constructor",
            StaticFiles(directory=str(directory), html=True),
            name="constructor_skin",
        )
