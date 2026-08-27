"""Compile Constructor canvas nodes into a Cortex IR + ranked approaches."""

from __future__ import annotations

from typing import Any

CORTEX_KIND: dict[str, str] = {
    "ingest": "DOCUMENT_REF",
    "connector": "DOCUMENT_REF",
    "ontology": "DOCUMENT_REF",
    "insight": "DOCUMENT_REF",
    "foundry": "DOCUMENT_REF",
    "app": "EMIT",
    "agent": "AGENT_TASK",
    "hypothesize": "DOCUMENT_REF",
    "improve": "DOCUMENT_REF",
    "audit": "DOCUMENT_REF",
    "tool_call": "TOOL_CALL",
}

APPROACHES: tuple[dict[str, Any], ...] = (
    {
        "id": "single_agent",
        "name": "Single agent",
        "cortex_status": "strong",
        "cortex_path": "AGENT_TASK max_steps loop",
        "cost": 1,
        "audit": 3,
        "blast": 1,
        "parked": False,
        "blurb": "One context, one tool loop. Default unless the graph has independent facets.",
    },
    {
        "id": "generator_verifier",
        "name": "Generator-verifier",
        "cortex_status": "partial",
        "cortex_path": "LLM_JUDGED then EMIT audit",
        "cost": 2,
        "audit": 5,
        "blast": 1,
        "parked": False,
        "blurb": "Generate, then verify against explicit audit criteria. Best when wrong output is expensive.",
    },
    {
        "id": "orchestrator_subagent",
        "name": "Orchestrator-subagent",
        "cortex_status": "strong",
        "cortex_path": "compile_template -> dag_runner + AGENT_TASK",
        "cost": 4,
        "audit": 4,
        "blast": 2,
        "parked": False,
        "blurb": "Lead plans, bounded subagents return distilled results. Use for ontology -> insights -> foundry -> app.",
    },
)


def _nodes(body: dict[str, Any]) -> list[dict[str, Any]]:
    raw = body.get("nodes") or []
    return [n for n in raw if isinstance(n, dict) and n.get("id")]


def _edges(body: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for e in body.get("edges") or []:
        if isinstance(e, dict) and e.get("from") and e.get("to"):
            out.append({"from": str(e["from"]), "to": str(e["to"])})
    return out


def topo(nodes: list[dict[str, Any]], edges: list[dict[str, str]]) -> list[str]:
    incoming: dict[str, int] = {str(n["id"]): 0 for n in nodes}
    for e in edges:
        incoming[e["to"]] = incoming.get(e["to"], 0) + 1
    q = [str(n["id"]) for n in nodes if incoming.get(str(n["id"]), 0) == 0]
    order: list[str] = []
    while q:
        nid = q.pop(0)
        order.append(nid)
        for e in edges:
            if e["from"] != nid:
                continue
            incoming[e["to"]] = incoming.get(e["to"], 0) - 1
            if incoming.get(e["to"], 0) == 0:
                q.append(e["to"])
    for n in nodes:
        nid = str(n["id"])
        if nid not in order:
            order.append(nid)
    return order


def compile_ir(body: dict[str, Any], *, ghost: bool = True) -> dict[str, Any]:
    nodes = _nodes(body)
    edges = _edges(body)
    if not nodes:
        return {
            "ok": False,
            "error": "graph has no nodes",
            "version": "1.0",
            "engine": "cortex",
            "ghost": ghost,
            "nodes": [],
            "edges": [],
        }
    output = next((n for n in reversed(nodes) if n.get("kind") == "app"), None)
    if output is None:
        output = next((n for n in reversed(nodes) if n.get("kind") == "audit"), nodes[-1])
    compiled: list[dict[str, Any]] = []
    for n in nodes:
        kind = CORTEX_KIND.get(str(n.get("kind") or ""), "DOCUMENT_REF")
        if str(n.get("id")) == str(output["id"]):
            kind = "EMIT"
        elif kind == "EMIT":
            kind = "DETERMINISTIC_RULE"
        compiled.append(
            {
                "id": str(n["id"]),
                "kind": kind,
                "constructor_kind": n.get("kind"),
                "object_type": n.get("object_type"),
                "data_point": n.get("data_point"),
                "data_type": n.get("data_type"),
                "action_type": n.get("action_type"),
                "fetch_from": n.get("fetch_from"),
                "tier": n.get("tier") or "T0",
                "stream": bool(n.get("stream")),
                "note": n.get("note"),
                "requires_confirm": n.get("kind") == "tool_call",
            }
        )
    return {
        "ok": True,
        "version": "1.0",
        "engine": "cortex",
        "ghost": ghost,
        "entry_node_id": str(nodes[0]["id"]),
        "output_node_id": str(output["id"]),
        "nodes": compiled,
        "edges": edges,
    }


def ghost_walk(body: dict[str, Any]) -> dict[str, Any]:
    ir = compile_ir(body, ghost=True)
    if not ir.get("ok"):
        return ir
    by_id = {n["id"]: n for n in ir["nodes"]}
    walked = []
    for nid in topo(ir["nodes"], ir["edges"]):
        node = by_id.get(nid)
        if not node:
            continue
        walked.append(
            {
                "id": nid,
                "kind": node["constructor_kind"],
                "cortex": node["kind"],
                "ghost": True,
                "write": False,
                "would": node.get("note"),
                "action_type": node.get("action_type"),
                "object_type": node.get("object_type"),
                "data_point": node.get("data_point"),
                "fetch_from": node.get("fetch_from"),
            }
        )
    ir["walk"] = walked
    return ir


def _status_weight(status: str) -> int:
    if status == "strong":
        return 4
    if status == "partial":
        return 2
    return 0


def recommend(body: dict[str, Any]) -> dict[str, Any]:
    kinds = {str(n.get("kind")) for n in _nodes(body)}
    foundry = {"ontology", "insight", "foundry", "app"}.issubset(kinds)
    verify = "hypothesize" in kinds and "audit" in kinds and not foundry
    ranked: list[dict[str, Any]] = []
    for row in APPROACHES:
        score = row["audit"] * 2 + _status_weight(str(row["cortex_status"])) - row["cost"] - row["blast"]
        if foundry and row["id"] == "orchestrator_subagent":
            score += 20
        if verify and row["id"] == "generator_verifier":
            score += 20
        ranked.append({**row, "score": score})
    ranked.sort(key=lambda r: r["score"], reverse=True)
    winner = ranked[0]
    return {
        "ok": True,
        "approaches": ranked,
        "recommendation": {"pattern": winner["id"], "name": winner["name"], "score": winner["score"]},
    }
