"""Discovery API — find skills / MCP / subagents for a goal."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class FindIn(BaseModel):
    goal: str = Field(..., min_length=1, description="What you need to accomplish")
    top_k: int = Field(8, ge=1, le=50)
    evolve: bool = False


class ToolSearchIn(BaseModel):
    query: str = ""
    top_k: int = Field(8, ge=1, le=50)


class ToolInjectIn(BaseModel):
    name: str = Field(..., min_length=1)


@router.get("/sources")
async def discovery_sources(caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    _ = caller
    from CortexOS.discovery.catalog import load_sources

    return {"ok": True, **load_sources()}


@router.get("/tools/deferred")
async def discovery_tools_deferred(
    caller: Caller = Depends(require_role("viewer")),
    limit: int = 50,
) -> dict[str, Any]:
    """Names-only tool/skill catalog (Claude Code deferred tools pattern)."""
    _ = caller
    from CortexOS.api.mcp_routes import _tool_catalog
    from CortexOS.execution.deferred_tools import catalog_from_mcp_tools

    cat = catalog_from_mcp_tools(_tool_catalog())
    return {
        "ok": True,
        "policy": "deferred_names_only",
        "projected_cost_tokens": cat.projected_cost_tokens(),
        "tools": cat.names_only(limit=limit),
    }


@router.post("/tools/search")
async def discovery_tools_search(
    body: ToolSearchIn,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    _ = caller
    from CortexOS.api.mcp_routes import _tool_catalog
    from CortexOS.execution.deferred_tools import catalog_from_mcp_tools

    cat = catalog_from_mcp_tools(_tool_catalog())
    return {"ok": True, "matches": cat.search(body.query, top_k=body.top_k)}


@router.post("/tools/inject")
async def discovery_tools_inject(
    body: ToolInjectIn,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    """Inject full schema for one deferred tool (logged as deferred_tools_delta)."""
    _ = caller
    from CortexOS.api.mcp_routes import _tool_catalog
    from CortexOS.execution.deferred_tools import catalog_from_mcp_tools

    tools = {t["name"]: t for t in _tool_catalog()}
    if body.name not in tools:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail={"ok": False, "error": "unknown_tool", "name": body.name})
    cat = catalog_from_mcp_tools(list(tools.values()))
    return cat.inject(body.name, schema=tools[body.name])


@router.post("/find")
async def discovery_find(
    body: FindIn,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    """Skills-first discovery for a goal (also returns MCP/subagent fillers)."""
    _ = caller
    from CortexOS.discovery.find import discover_for_goal

    return discover_for_goal(body.goal, top_k=body.top_k, evolve=body.evolve).as_dict()


@router.post("/find-skills")
async def discovery_find_skills(
    body: FindIn,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    _ = caller
    from CortexOS.discovery.find import find_skills

    return find_skills(body.goal, top_k=body.top_k, evolve=body.evolve)


@router.post("/find-mcp")
async def discovery_find_mcp(
    body: FindIn,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    _ = caller
    from CortexOS.discovery.find import find_mcp

    return find_mcp(body.goal, top_k=body.top_k)


@router.post("/find-subagents")
async def discovery_find_subagents(
    body: FindIn,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    _ = caller
    from CortexOS.discovery.find import find_subagents

    return find_subagents(body.goal, top_k=body.top_k)


@router.get("/playground")
async def discovery_playground(caller: Caller = Depends(require_role("viewer"))) -> HTMLResponse:
    """Minimal same-origin HTML for Playwright browser reliability checks."""
    _ = caller
    html = """<!doctype html>
<html><head><meta charset="utf-8"><title>Find Skills Playground</title></head>
<body>
  <h1>Find Skills</h1>
  <input id="goal" data-testid="goal" value="playwright testing" />
  <button id="go" data-testid="go">Find</button>
  <pre id="out" data-testid="out"></pre>
  <script>
    document.getElementById('go').onclick = async () => {
      const goal = document.getElementById('goal').value;
      const res = await fetch('/api/discovery/find-skills', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({goal, top_k: 3})
      });
      const data = await res.json();
      document.getElementById('out').textContent = data.best ? data.best.name : 'NONE';
    };
  </script>
</body></html>"""
    return HTMLResponse(html)


def register_discovery_routes(app: Any) -> None:
    app.include_router(router)
