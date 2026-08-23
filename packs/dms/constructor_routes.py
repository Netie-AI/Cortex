"""Key-gated Constructor skin + compile-run on /cortex.

Public URL (existing host, not a new name): https://app.netie.ai/cortex
Local: http://127.0.0.1:8010/cortex
HTML GET needs a valid API key cookie or X-API-Key. Login form is the only open page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from CortexOS.paths import constructor_skin_dir
from packs.dms.security.api_auth import (
    Caller,
    extract_api_key,
    resolve_caller,
    role_at_least,
)

COOKIE = "cortex_api_key"
PREFIX = "/cortex"
SKIN_NAMES = frozenset({"index.html", "app.js", "styles.css", "engine.js", "README.md"})

router = APIRouter(tags=["constructor"])


class SessionBody(BaseModel):
    key: str = Field(min_length=1)


class ConstructorRunBody(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]] = Field(default_factory=list)
    run_id: str = "constructor_run"


class IssueKeyBody(BaseModel):
    label: str = "constructor cortex viewer"
    tier: str = "free"


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _require_loopback(request: Request, action: str) -> None:
    host = (request.client.host if request.client else "") or ""
    if host not in _LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail=f"{action} is loopback-only")


def _skin_dir() -> Path:
    return constructor_skin_dir()


def _caller_from_request(
    request: Request,
    x_api_key: str | None,
    authorization: str | None,
) -> Caller:
    key = extract_api_key(x_api_key, authorization) or request.cookies.get(COOKIE)
    caller = resolve_caller(key)
    if caller is None:
        raise HTTPException(status_code=401, detail="Valid API key required (X-API-Key, Bearer, or session cookie)")
    return caller


async def require_constructor_viewer(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> Caller:
    caller = _caller_from_request(request, x_api_key, authorization)
    if not role_at_least(caller.role, "viewer"):
        raise HTTPException(status_code=403, detail="Requires viewer or higher")
    return caller


_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Cortex key</title>
<style>body{font-family:Segoe UI,sans-serif;background:#050505;color:#e5e5e5;margin:2rem}
label,input,button{display:block;margin:.5rem 0}input{padding:.5rem;min-width:20rem}
button{padding:.5rem .8rem;background:#111;color:#e5e5e5;border:1px solid #333}</style>
</head><body>
<h1>Cortex</h1>
<p>Engine path. Paste an OpenVault issued key (ov_...). Keys live in OpenVault, not Cortex.</p>
<form id="login" method="post" action="/cortex/session">
<label for="key">OpenVault key</label>
<input id="key" name="key" type="password" autocomplete="off" required/>
<button type="submit">Continue</button>
</form>
<p><button type="button" id="issue">Generate OpenVault key</button></p>
<pre id="once"></pre>
<script>
document.getElementById("login").onsubmit = async function (e) {
  e.preventDefault();
  var once = document.getElementById("once");
  var r = await fetch("/cortex/session", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    credentials: "same-origin",
    body: JSON.stringify({key: document.getElementById("key").value})
  });
  if (r.ok || r.redirected || r.status === 303) {
    window.location.href = "/cortex/constructor/";
    return;
  }
  once.textContent = r.status + " " + await r.text();
};
document.getElementById("issue").onclick = async function () {
  var once = document.getElementById("once");
  once.textContent = "Issuing...";
  var r = await fetch("/cortex/constructor/issue-key", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: "{}"
  });
  var j = await r.json();
  if (!j.token) {
    once.textContent = r.status + " " + JSON.stringify(j);
    return;
  }
  document.getElementById("key").value = j.token;
  once.textContent = "Shown once. Token is in the box. Continue. Lost keys cannot be recovered.";
};
</script>
</body></html>
"""


@router.get("/cortex/login", response_class=HTMLResponse)
def cortex_login() -> HTMLResponse:
    return HTMLResponse(_LOGIN_HTML)


@router.post("/cortex/constructor/issue-key")
def constructor_issue_key(request: Request, body: IssueKeyBody = IssueKeyBody()) -> dict[str, Any]:
    """Loopback mint. OpenVault holds the secret. Cortex never stores it."""
    _require_loopback(request, "issue OpenVault key")
    from CortexOS.integrations.openvault_client import post_json

    payload = {
        "label": body.label.strip() or "constructor cortex viewer",
        "tier": body.tier.strip() or "free",
    }
    out = post_json("/api/apikeys", payload, timeout=5.0)
    token = str((out or {}).get("token") or (out or {}).get("token") or "").strip()
    if not out or not token:
        raise HTTPException(status_code=503, detail="OpenVault did not issue a key")
    out = dict(out)
    out["token"] = token
    return out


@router.post("/cortex/session", response_model=None)
async def cortex_session(request: Request) -> RedirectResponse:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = SessionBody.model_validate(await request.json())
        key = body.key.strip()
    else:
        try:
            form = await request.form()
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="Send JSON {key: ...} (form parse failed)",
            ) from exc
        key = str(form.get("key") or "").strip()
    caller = resolve_caller(key)
    if caller is None:
        raise HTTPException(status_code=401, detail="Valid API key required")
    secure = request.url.scheme == "https"
    dest = PREFIX + "/constructor/"
    resp = RedirectResponse(url=dest, status_code=303)
    resp.set_cookie(
        COOKIE,
        key,
        httponly=True,
        samesite="lax",
        secure=secure,
        path=PREFIX,
        max_age=12 * 3600,
    )
    return resp


@router.post("/cortex/session/clear", response_model=None)
def cortex_session_clear() -> RedirectResponse:
    resp = RedirectResponse(url=PREFIX + "/login", status_code=303)
    resp.delete_cookie(COOKIE, path=PREFIX)
    return resp


@router.get("/cortex", response_model=None)
@router.get("/cortex/", response_model=None)
def cortex_root(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> RedirectResponse:
    try:
        _caller_from_request(request, x_api_key, authorization)
    except HTTPException:
        return RedirectResponse(url=PREFIX + "/login", status_code=303)
    return RedirectResponse(url=PREFIX + "/constructor/", status_code=307)


def _skin_file(name: str) -> Path:
    if name not in SKIN_NAMES:
        raise HTTPException(status_code=404, detail="not found")
    path = _skin_dir() / name
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Constructor skin missing (set CONSTRUCTOR_SKIN_DIR)")
    return path


@router.get("/cortex/constructor", response_model=None)
@router.get("/cortex/constructor/", response_model=None)
def constructor_index(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> FileResponse | RedirectResponse:
    try:
        _caller_from_request(request, x_api_key, authorization)
    except HTTPException:
        return RedirectResponse(url=PREFIX + "/login", status_code=303)
    return FileResponse(_skin_file("index.html"))


@router.get("/cortex/constructor/ontology")
def constructor_ontology(caller: Caller = Depends(require_constructor_viewer)) -> dict[str, Any]:
    from packs.dms.constructor_fetch import catalog

    _ = caller
    return {"ok": True, **catalog()}


@router.get("/cortex/constructor/{name}", response_model=None)
def constructor_asset(
    name: str,
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> FileResponse | RedirectResponse:
    try:
        _caller_from_request(request, x_api_key, authorization)
    except HTTPException:
        return RedirectResponse(url=PREFIX + "/login", status_code=303)
    return FileResponse(_skin_file(name))


@router.post("/cortex/constructor/fetch")
def constructor_fetch(
    body: ConstructorRunBody,
    caller: Caller = Depends(require_constructor_viewer),
) -> dict[str, Any]:
    from packs.dms.constructor_fetch import fetch_slice

    _ = caller
    node = next((n for n in body.nodes if isinstance(n, dict)), None)
    if node is None:
        raise HTTPException(status_code=400, detail="nodes must include one object")
    slice_ = fetch_slice(
        object_type=node.get("object_type"),
        data_point=node.get("data_point"),
        data_type=node.get("data_type"),
        fetch_from=node.get("fetch_from"),
        stream=bool(node.get("stream")),
    )
    return {"ok": True, "slice": slice_}


@router.post("/cortex/constructor/ghost")
def constructor_ghost(
    body: ConstructorRunBody,
    caller: Caller = Depends(require_constructor_viewer),
) -> dict[str, Any]:
    from CortexOS.constructor_graph import ConstructorGraphError, compile_constructor_graph

    try:
        program = compile_constructor_graph({"nodes": body.nodes, "edges": body.edges})
    except ConstructorGraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "ghost": True,
        "actor": caller.actor,
        "entry_node_id": program.entry_node_id,
        "output_node_id": program.output_node_id,
        "nodes": [
            {
                "id": n.id,
                "kind": n.type.value if hasattr(n.type, "value") else str(n.type),
                "constructor_kind": (n.annotations or {}).get("constructor_kind"),
                "inputs": n.inputs,
            }
            for n in program.nodes
        ],
    }


@router.post("/cortex/constructor/recommend")
def constructor_recommend(
    body: ConstructorRunBody,
    caller: Caller = Depends(require_constructor_viewer),
) -> dict[str, Any]:
    from CortexOS.constructor_graph import recommend_extras
    from CortexOS.execution import coordination_patterns

    kinds = [str(n.get("kind") or "") for n in body.nodes]
    prompt = " ".join(kinds) or "foundry ontology insight app"
    rec = coordination_patterns.recommend_from_prompt(prompt, extras=recommend_extras(kinds))
    wanted = {"single_agent", "generator_verifier", "orchestrator_subagent"}
    approaches = [row for row in coordination_patterns.catalog() if row["id"] in wanted]
    _ = caller
    return {"ok": True, "recommendation": rec.as_dict(), "approaches": approaches}


@router.post("/cortex/constructor/run")
async def constructor_run(
    request: Request,
    body: ConstructorRunBody,
    caller: Caller = Depends(require_constructor_viewer),
) -> dict[str, Any]:
    from netie.execution.dag_runner import ExecutionContext, run_dag
    from netie.execution.model_router import ModelRouter

    from CortexOS.constructor_graph import ConstructorGraphError, compile_constructor_graph

    try:
        program = compile_constructor_graph({"nodes": body.nodes, "edges": body.edges})
    except ConstructorGraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ledger = getattr(request.app.state, "ledger", None)
    if ledger is None:
        from CortexOS.routing.cost_ledger import CostLedger

        ledger = CostLedger()

    router_m = getattr(request.app.state, "model_router", None)
    if not isinstance(router_m, ModelRouter):
        router_m = ModelRouter()

    from packs.dms.constructor_fetch import fetch_slice

    seed: dict[str, Any] = {"actor": caller.actor}
    fetches: dict[str, Any] = {}
    for node in program.nodes:
        ann = node.annotations if isinstance(node.annotations, dict) else {}
        if not (
            node.context_key
            or ann.get("fetch_from")
            or ann.get("object_type")
            or ann.get("stream")
        ):
            continue
        slice_ = fetch_slice(
            object_type=ann.get("object_type"),
            data_point=ann.get("data_point"),
            data_type=ann.get("data_type"),
            fetch_from=ann.get("fetch_from"),
            stream=bool(ann.get("stream")),
        )
        key = node.context_key or node.id
        seed[key] = slice_
        fetches[node.id] = {
            "table": slice_.get("table"),
            "row_count": slice_.get("row_count"),
            "error": slice_.get("error"),
            "stream": slice_.get("stream"),
        }

    ctx = ExecutionContext(body.run_id, seed)
    try:
        dag_result = await run_dag(program, ctx, router_m, ledger)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    serialized: dict[str, Any] = {}
    for nid, nr in dag_result.outputs.items():
        serialized[nid] = {"output": nr.output, "tier": nr.tier, "cost_myr": nr.cost_myr}
    return {
        "ok": True,
        "run_id": body.run_id,
        "actor": caller.actor,
        "nodes": serialized,
        "fetches": fetches,
    }


def register_constructor_routes(app: Any) -> None:
    app.include_router(router)
