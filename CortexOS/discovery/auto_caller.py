"""Map a goal to first-party Cortex/AirGPT tools. Community MCP stays P16-parked."""

from __future__ import annotations

from typing import Any

# First-party HTTP tools Cortex actually exposes today (not catalog wish-list).
_FIRST_PARTY: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "constructor.ghost",
        ("constructor", "canvas", "foundry", "workflow", "compile graph", "ontology"),
        "Compile a Constructor canvas without running LLMs.",
    ),
    (
        "constructor.recommend",
        ("approach", "pattern", "orchestrator", "propose 3"),
        "Rank live Constructor coordination patterns.",
    ),
    (
        "memory.query",
        ("memory", "recall", "persist", "remember", "knn", "rawknn"),
        "Query persistent Cortex memory.",
    ),
    (
        "answer_engine.answer",
        ("warehouse", "inventory", "dms answer", "lakehouse question"),
        "Governed DMS answer over warehouse data.",
    ),
    (
        "lakehouse.tables",
        ("tables", "lakehouse"),
        "List lakehouse tables.",
    ),
    (
        "find_skills",
        ("skill", "skillcard"),
        "Find local/curated skills for a goal.",
    ),
    (
        "find_mcp",
        ("mcp", "connector", "catalog"),
        "Search the offline MCP reference catalog (install stays P16).",
    ),
    (
        "find_subagents",
        ("subagent", "toolkit"),
        "Find curated subagent refs.",
    ),
    (
        "agent.status",
        ("engine status", "runtime"),
        "Cortex engine status snapshot.",
    ),
)

_AIRGPT: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "constructor_ghost",
        ("constructor", "canvas", "foundry", "workflow"),
        "AirGPT sidecar: POST /cortex/constructor/ghost",
    ),
    (
        "dms_query",
        ("warehouse", "inventory", "supplier", "shipment", "dms object"),
        "AirGPT sidecar: read DMS objects.",
    ),
    (
        "dms_action",
        ("export", "pptx", "dms action"),
        "AirGPT sidecar: governed DMS action (confirm required).",
    ),
    (
        "memory_search",
        ("memory", "recall", "remember"),
        "AirGPT local memory search.",
    ),
    (
        "web_search",
        ("research web", "search the web"),
        "AirGPT web search.",
    ),
)

_BANNED_SEND = ("twilio", "pywhatkit", "baileys", "whatsapp-web.js", "selenium")


def _score(goal: str, keywords: tuple[str, ...]) -> int:
    g = goal.lower()
    return sum(1 for kw in keywords if kw in g)


def _rank(goal: str, table: tuple[tuple[str, tuple[str, ...], str], ...]) -> list[dict[str, Any]]:
    scored = []
    for name, keywords, why in table:
        n = _score(goal, keywords)
        if n:
            scored.append({"name": name, "score": n, "why": why})
    scored.sort(key=lambda row: (-row["score"], row["name"]))
    return scored


def pick(goal: str, *, top_k: int = 5) -> dict[str, Any]:
    """Research auto-caller: first-party tools first; WhatsApp send is never live."""
    g = (goal or "").strip()
    if not g:
        return {"ok": False, "error": "goal required"}

    first = _rank(g, _FIRST_PARTY)
    airgpt = _rank(g, _AIRGPT)
    lowered = g.lower()
    whatsapp = "whatsapp" in lowered or "wa.me" in lowered

    community: list[dict[str, Any]] = []
    if whatsapp or "connector" in lowered or "mcp" in lowered:
        from CortexOS.discovery.find import find_mcp

        found = find_mcp(g, top_k=top_k)
        for hit in found.get("matches") or []:
            row = hit if isinstance(hit, dict) else {}
            community.append(
                {
                    "id": row.get("id") or "",
                    "name": row.get("name") or "",
                    "install_hint": row.get("install_hint") or "",
                    "p16_parked": True,
                }
            )

    if whatsapp:
        first = [
            {
                "name": "brain.draft_whatsapp",
                "score": 99,
                "why": "POST /dms/brain/whatsapp drafts only; requires_confirm=True; no send.",
                "path": "/dms/brain/whatsapp",
            }
        ] + [row for row in first if row["name"] != "find_mcp"]
        first.insert(
            1,
            {
                "name": "find_mcp",
                "score": 2,
                "why": "Catalog may list WhatsApp MCP servers; connecting them stays P16-parked.",
            },
        )

    return {
        "ok": True,
        "goal": g,
        "first_party": first[:top_k],
        "airgpt_tools": airgpt[:top_k],
        "community_mcp": community[:top_k],
        "live_whatsapp_connector": False,
        "p16_third_party_mcp": True,
        "banned_send_libs": list(_BANNED_SEND),
        "warning": (
            "WhatsApp is a draft route, not a live connector. Community WhatsApp MCP "
            "hits stay parked until P16."
            if whatsapp
            else "Third-party MCP clients stay parked until P16."
        ),
    }
