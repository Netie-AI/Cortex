"""Key-gated Constructor skin + compile-run on /cortex.

Public URL (existing host, not a new name): https://app.netie.ai/cortex
Local: http://127.0.0.1:8010/cortex
HTML GET needs a valid API key cookie or X-API-Key. Login form is the only open page.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

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


def _skin_dir() -> Path:
    raw = (os.environ.get("CONSTRUCTOR_SKIN_DIR") or r"D:\Constructor").strip()
    return Path(raw)


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
<p>Engine path. Paste an API key. No key, no access.</p>
<form method="post" action="/cortex/session">
<label for="key">API key</label>
<input id="key" name="key" type="password" autocomplete="off" required/>
<button type="submit">Continue</button>
</form>
</body></html>
"""


@router.get("/cortex/login", response_class=HTMLResponse)
def cortex_login() -> HTMLResponse:
    return HTMLResponse(_LOGIN_HTML)


@router.post("/cortex/session", response_model=None)
async def cortex_session(request: Request) -> RedirectResponse:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = SessionBody.model_validate(await request.json())
        key = body.key.strip()
    else:
        form = await request.form()
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


@router.post("/cortex/constructor/run")
async def constructor_run(
    request: Request,
    body: ConstructorRunBody,
    caller: Caller = Depends(require_constructor_viewer),
) -> dict[str, Any]:
    from CortexOS.constructor_graph import ConstructorGraphError, compile_constructor_graph
    from netie.execution.dag_runner import ExecutionContext, run_dag
    from netie.execution.model_router import ModelRouter

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

    ctx = ExecutionContext(body.run_id, {"actor": caller.actor})
    dag_result = await run_dag(program, ctx, router_m, ledger)
    serialized: dict[str, Any] = {}
    for nid, nr in dag_result.outputs.items():
        serialized[nid] = {"output": nr.output, "tier": nr.tier, "cost_myr": nr.cost_myr}
    return {"run_id": body.run_id, "actor": caller.actor, "nodes": serialized}


def register_constructor_routes(app: Any) -> None:
    app.include_router(router)
