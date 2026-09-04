"""Read-only first-party MCP-shaped tool surface (HTTP).

Exposes Cortex tools for external clients without loading third-party MCP
servers. Includes Find Skills discovery over curated awesome-list catalogs.
Third-party MCP *clients* remain gated behind P16.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/mcp", tags=["mcp"])


class McpCallIn(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


def _tool_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": "answer_engine.answer",
            "description": "Governed DMS semantic/RAG answer over warehouse data.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "session_id": {"type": "string"},
                    "space_id": {"type": "string"},
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
        {
            "name": "computer_control.status",
            "description": (
                "Probe UACC / computer-control-mcp / Windows-MCP. Read-only. "
                "Does not move mouse or keyboard. Default disarmed."
            ),
            "inputSchema": {"type": "object", "properties": {}},
            "read_only": True,
        },
        {
            "name": "computer_control.invoke",
            "description": (
                "Gated computer-control action (status|screenshot|click|type|move). "
                "Fail-closed unless CORTEX_COMPUTER_CONTROL=1 and a driver imports. "
                "This host still will not move input in-process; use a uacc-mcp sidecar."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["action"],
            },
            "read_only": False,
        },
        {
            "name": "auto_caller.pick",
            "description": (
                "Research auto-caller: pick first-party Cortex/AirGPT tools for a goal. "
                "Community WhatsApp MCP is listed then P16-parked; no live send."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["goal"],
            },
            "read_only": True,
        },
        {
            "name": "constructor.ghost",
            "description": "Compile a Constructor canvas into a Cortex DAG (no LLM run).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "nodes": {"type": "array"},
                    "edges": {"type": "array"},
                },
                "required": ["nodes"],
            },
            "read_only": True,
        },
        {
            "name": "constructor.recommend",
            "description": "Recommend live Constructor coordination patterns.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "nodes": {"type": "array"},
                    "edges": {"type": "array"},
                },
            },
            "read_only": True,
        },
        {
            "name": "memory.query",
            "description": "Query the Cortex persistent memory store.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "vector": {"type": "array"},
                    "k": {"type": "integer", "default": 5},
                    "scope": {"type": "string"},
                    "collection": {"type": "string"},
                },
                "required": ["vector"],
            },
            "read_only": True,
        },
    ]


@router.get("/tools")
async def mcp_list_tools(caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
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
) -> dict[str, Any]:
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
        from CortexOS.dms.answer_engine import UngroundedSession
        from CortexOS.dms.answer_engine import answer as answer_fn
        from CortexOS.execution.session_manifests import (
            SessionExpired,
            SessionUnbound,
            SpaceUnbound,
        )

        space_raw = args.get("space_id")
        space_id = str(space_raw).strip() if space_raw else None
        try:
            result = answer_fn(
                question,
                session_id=args.get("session_id"),
                space_id=space_id,
                require_grounding=True,
            )
        except (UngroundedSession, SpaceUnbound, SessionUnbound, SessionExpired) as exc:
            result = {
                "answer": str(exc),
                "sql_used": None,
                "badge": "abstain",
                "route": "needs_clarification",
                "rows": [],
                "grant_kind": "none",
                "granted_sources": [],
            }
        return {"ok": True, "name": name, "result": result}

    if name == "lakehouse.tables":
        tables: list[str] = []
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
        from netie.engine import registry

        from CortexOS.execution import architecture_presets

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

    if name == "auto_caller.pick":
        from CortexOS.discovery.auto_caller import pick

        goal = str(args.get("goal") or args.get("query") or "").strip()
        if not goal:
            raise HTTPException(status_code=400, detail={"ok": False, "error": "goal required"})
        return {"ok": True, "name": name, "result": pick(goal, top_k=int(args.get("top_k") or 5))}

    if name == "constructor.ghost":
        from CortexOS.constructor_graph import ConstructorGraphError, compile_constructor_graph

        try:
            program = compile_constructor_graph(
                {"nodes": args.get("nodes") or [], "edges": args.get("edges") or []}
            )
        except ConstructorGraphError as exc:
            raise HTTPException(status_code=400, detail={"ok": False, "error": str(exc)}) from exc
        return {
            "ok": True,
            "name": name,
            "result": {
                "ghost": True,
                "entry_node_id": program.entry_node_id,
                "output_node_id": program.output_node_id,
                "node_count": len(program.nodes),
            },
        }

    if name == "constructor.recommend":
        from CortexOS.constructor_graph import recommend_extras
        from CortexOS.execution import coordination_patterns

        kinds = [str(n.get("kind") or "") for n in (args.get("nodes") or [])]
        prompt = " ".join(kinds) or "foundry ontology insight app"
        rec = coordination_patterns.recommend_from_prompt(prompt, extras=recommend_extras(kinds))
        wanted = {"single_agent", "generator_verifier", "orchestrator_subagent"}
        approaches = [row for row in coordination_patterns.catalog() if row["id"] in wanted]
        return {
            "ok": True,
            "name": name,
            "result": {"recommendation": rec.as_dict(), "approaches": approaches},
        }

    if name == "memory.query":
        from CortexOS.api import memory_routes

        vector = args.get("vector")
        if not isinstance(vector, list) or not vector:
            raise HTTPException(status_code=400, detail={"ok": False, "error": "vector required"})
        hits = memory_routes._STORE.query(
            vector,
            k=int(args.get("k") or 5),
            scope=args.get("scope") if args.get("scope") in ("personal", "company") else None,
            collection=args.get("collection"),
        )
        return {
            "ok": True,
            "name": name,
            "result": {
                "hits": [
                    {"id": h.id, "score": round(h.score, 6), "text": h.text, "meta": h.meta}
                    for h in hits
                ]
            },
        }

    if name == "computer_control.status":
        from CortexOS.connectors import computer_control as cc

        return {"ok": True, "name": name, "result": cc.probe()}

    if name == "computer_control.invoke":
        from CortexOS.connectors import computer_control as cc

        action = str(args.get("action") or "").strip()
        payload = {k: args[k] for k in ("x", "y", "text") if k in args}
        out = cc.invoke(action, **payload)
        return {"ok": bool(out.get("ok")), "name": name, "result": out}

    raise HTTPException(status_code=404, detail={"ok": False, "error": "unknown_tool"})


def register_mcp_routes(app: Any) -> None:
    app.include_router(router)
