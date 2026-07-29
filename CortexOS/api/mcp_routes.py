"""Read-only first-party MCP-shaped tool surface (HTTP).

Exposes Cortex tools for external clients without loading third-party MCP
servers. Includes Find Skills discovery over curated awesome-list catalogs.
Third-party MCP *clients* remain gated behind P16.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/mcp", tags=["mcp"])


class McpCallIn(BaseModel):
    name: str
    arguments: Dict[str, Any] = {}


def _tool_catalog() -> List[Dict[str, Any]]:
    return [
        {
            "name": "answer_engine.answer",
            "description": "Governed DMS semantic/RAG answer over warehouse data.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "session_id": {"type": "string"},
                },
                "required": ["question"],
            },
            "read_only": True,
        },
        {
            "name": "lakehouse.tables",
            "description": "List DuckLake / lakehouse tables visible to the caller.",
            "inputSchema": {"type": "object", "properties": {}},
            "read_only": True,
        },
        {
            "name": "agent.status",
            "description": "Agent runtime / engine status snapshot.",
            "inputSchema": {"type": "object", "properties": {}},
            "read_only": True,
        },
        {
            "name": "find_skills",
            "description": (
                "Find the best agent skills for a goal (skills first, then MCP/subagents). "
                "Ask: 'Are there any good skills for [GOAL]?' Uses curated GitHub awesome-list "
                "references + local SkillCards; optionally seeds SkillOpt evolution."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "top_k": {"type": "integer", "default": 8},
                    "evolve": {"type": "boolean", "default": False},
                },
                "required": ["goal"],
            },
            "read_only": True,
        },
        {
            "name": "find_mcp",
            "description": "Find MCP servers from the awesome-mcp-servers reference catalog.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "top_k": {"type": "integer", "default": 8},
                },
                "required": ["goal"],
            },
            "read_only": True,
        },
        {
            "name": "find_subagents",
            "description": "Find subagents / toolkit entries from awesome Claude Code toolkit refs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "top_k": {"type": "integer", "default": 8},
                },
                "required": ["goal"],
            },
            "read_only": True,
        },
    ]


@router.get("/tools")
async def mcp_list_tools(caller: Caller = Depends(require_role("viewer"))) -> Dict[str, Any]:
    _ = caller
    return {
        "ok": True,
        "protocol": "cortex-mcp-http/0.1",
        "note": "First-party read-only tools. Third-party MCP clients stay parked until P16.",
        "tools": _tool_catalog(),
    }


@router.post("/call")
async def mcp_call_tool(
    body: McpCallIn,
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    _ = caller
    name = (body.name or "").strip()
    args = body.arguments or {}
    known = {t["name"] for t in _tool_catalog()}
    if name not in known:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "unknown_tool", "name": name})

    if name == "answer_engine.answer":
        question = str(args.get("question") or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail={"ok": False, "error": "question required"})
        from CortexOS.dms.answer_engine import answer as answer_fn

        result = answer_fn(question, session_id=args.get("session_id"))
        return {"ok": True, "name": name, "result": result}

    if name == "lakehouse.tables":
        tables: List[str] = []
        try:
            from CortexOS.dms import lakehouse

            if hasattr(lakehouse, "list_tables"):
                tables = list(lakehouse.list_tables())
            elif hasattr(lakehouse, "tables"):
                tables = list(lakehouse.tables())
        except Exception as exc:
            return {"ok": True, "name": name, "result": {"tables": [], "warning": str(exc)[:160]}}
        return {"ok": True, "name": name, "result": {"tables": tables}}

    if name == "agent.status":
        from CortexOS.execution import architecture_presets
        from netie.engine import registry

        return {
            "ok": True,
            "name": name,
            "result": {
                "engine": "cortex",
                "architecture_presets": [p["id"] for p in architecture_presets.catalog()],
                "backends": [b.id for b in registry.BACKENDS],
            },
        }

    if name in ("find_skills", "find_mcp", "find_subagents"):
        from CortexOS.discovery.find import DISCOVERY_TOOLS

        goal = str(args.get("goal") or args.get("query") or "").strip()
        if not goal:
            raise HTTPException(status_code=400, detail={"ok": False, "error": "goal required"})
        result = DISCOVERY_TOOLS[name](**args)
        return {"ok": True, "name": name, "result": result}

    raise HTTPException(status_code=404, detail={"ok": False, "error": "unknown_tool"})


def register_mcp_routes(app: Any) -> None:
    app.include_router(router)
