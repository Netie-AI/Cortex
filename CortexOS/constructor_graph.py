"""Compile Constructor canvas JSON onto an existing AgenticDSL program."""

from __future__ import annotations

import json
from typing import Any

from netie.fabrication.dsl_parser import AgenticDSLProgram, DSLNode, NodeType, parse_dsl
from netie.result import Ok

_KINDS = frozenset(
    {
        "ingest",
        "connector",
        "ontology",
        "insight",
        "foundry",
        "app",
        "agent",
        "hypothesize",
        "improve",
        "audit",
        "tool_call",
    }
)

_KIND_TO_TYPE = {
    "ingest": NodeType.DOCUMENT_REF,
    "connector": NodeType.DOCUMENT_REF,
    "ontology": NodeType.DOCUMENT_REF,
    "insight": NodeType.DOCUMENT_REF,
    "foundry": NodeType.DOCUMENT_REF,
    "app": NodeType.EMIT,
    "agent": NodeType.AGENT_TASK,
    "hypothesize": NodeType.DOCUMENT_REF,
    "improve": NodeType.DOCUMENT_REF,
    "audit": NodeType.DOCUMENT_REF,
    "tool_call": NodeType.TOOL_CALL,
}


class ConstructorGraphError(ValueError):
    pass


def compile_constructor_graph(payload: dict[str, Any]) -> AgenticDSLProgram:
    """Map {nodes, edges} to a parse_dsl-valid program. Exactly one EMIT. No new orchestrator."""
    nodes = payload.get("nodes")
    edges = payload.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise ConstructorGraphError("nodes must be a non-empty list")
    if not isinstance(edges, list):
        raise ConstructorGraphError("edges must be a list")

    by_id: dict[str, dict[str, Any]] = {}
    for raw in nodes:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            raise ConstructorGraphError("each node needs a string id")
        kind = raw.get("kind")
        if kind not in _KINDS:
            raise ConstructorGraphError(f"unknown kind {kind!r}")
        if raw["id"] in by_id:
            raise ConstructorGraphError(f"duplicate node id {raw['id']!r}")
        by_id[raw["id"]] = raw

    inbound: dict[str, list[str]] = {nid: [] for nid in by_id}
    for edge in edges:
        if not isinstance(edge, dict):
            raise ConstructorGraphError("edge must be an object")
        src, dst = edge.get("from"), edge.get("to")
        if src not in by_id or dst not in by_id:
            raise ConstructorGraphError("edge references missing node")
        if src == dst:
            raise ConstructorGraphError("self-edge not allowed")
        if src not in inbound[dst]:
            inbound[dst].append(src)

    apps = [n["id"] for n in nodes if n["kind"] == "app"]
    audits = [n["id"] for n in nodes if n["kind"] == "audit"]
    output = (apps or audits or [nodes[-1]["id"]])[-1]
    entry = next((n["id"] for n in nodes if not inbound[n["id"]]), nodes[0]["id"])

    dsl_nodes: list[DSLNode] = []
    for raw in nodes:
        nid = raw["id"]
        ntype = NodeType.EMIT if nid == output else _KIND_TO_TYPE[raw["kind"]]
        if ntype == NodeType.EMIT and nid != output:
            ntype = NodeType.DETERMINISTIC_RULE
        action = str(raw.get("action_type") or "")
        # Only export_pptx is an F8 tool. item.intake / agent.checked are ledger events.
        if ntype == NodeType.TOOL_CALL and action and action != "export_pptx":
            ntype = NodeType.DOCUMENT_REF
        fields: dict[str, Any] = {
            "id": nid,
            "kind": ntype,
            "inputs": inbound[nid],
            "prompt": str(raw.get("note") or raw["kind"]),
            "annotations": {
                "constructor_kind": raw["kind"],
                "object_type": raw.get("object_type"),
                "action_type": raw.get("action_type"),
                "data_point": raw.get("data_point"),
                "data_type": raw.get("data_type"),
                "fetch_from": raw.get("fetch_from"),
                "stream": bool(raw.get("stream")),
            },
        }
        if ntype == NodeType.DOCUMENT_REF:
            fields["context_key"] = nid
        tier = str(raw.get("tier") or "T0").upper()
        if tier not in ("T0", "T1"):
            tier = "T0"
        if ntype in (NodeType.LLM_JUDGED, NodeType.AGENT_TASK, NodeType.RAG_ANSWER, NodeType.TOOL_CALL):
            fields["default_tier"] = tier
            fields["max_tier"] = "T1"
        if ntype == NodeType.TOOL_CALL:
            fields["tool_name"] = action or "export_pptx"
            fields["annotations"]["params"] = {
                "title": f"Constructor {raw.get('object_type') or ''} {raw.get('data_point') or ''}".strip(),
                "body": str(raw.get("fetch_from") or raw.get("object_type") or "constructor run"),
            }
        dsl_nodes.append(DSLNode.model_validate(fields))

    program = AgenticDSLProgram(
        version="1.0",
        nodes=dsl_nodes,
        entry_node_id=entry,
        output_node_id=output,
    )
    parsed = parse_dsl(json.dumps(_envelope(program)), "constructor")
    if not isinstance(parsed, Ok):
        raise ConstructorGraphError(parsed.message)
    return parsed.value


_OBJECT_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("inventory", ("inventory", "sku", "stock", "warehouse")),
    ("suppliers", ("supplier", "vendor")),
    ("locations", ("location", "site", "bin")),
    ("shipments", ("shipment", "consignment", "carrier")),
    ("transactions", ("transaction", "txn", "movement")),
    ("alerts", ("alert", "alarm")),
)

_DEFAULT_POINT: dict[str, str] = {
    "inventory": "sku",
    "suppliers": "supplier_id",
    "locations": "location_id",
    "shipments": "shipment_id",
    "transactions": "txn_id",
    "alerts": "alert_id",
}

_KIND_NOTE: dict[str, str] = {
    "ingest": "Read operations into the graph.",
    "connector": "First-party Cortex input. No n8n.",
    "ontology": "Cortex ontology objects/links/actions.",
    "insight": "Cite ontology + ledger.",
    "foundry": "Compile a governed Cortex app from insights.",
    "app": "Runnable output hosted inside Cortex.",
    "agent": "AGENT_TASK loop. One bounded worker.",
    "hypothesize": "Surface a testable claim.",
    "improve": "Change a product from the claim.",
    "audit": "Why this node exists. DETERMINISTIC_RULE, not a second EMIT.",
    "tool_call": "F8 governed write. requires_confirm.",
}


def objects_in_prompt(prompt: str) -> list[str]:
    low = (prompt or "").lower()
    found: list[str] = []
    for obj, words in _OBJECT_WORDS:
        if any(w in low for w in words) and obj not in found:
            found.append(obj)
    return found


def generate_constructor_graph(prompt: str) -> dict[str, Any]:
    """Chat -> canvas. Deterministic Cortex compiler, not an n8n import."""
    text = (prompt or "").strip()
    if not text:
        raise ConstructorGraphError("prompt is empty")
    low = text.lower()
    objects = objects_in_prompt(low)
    assumed = False
    if not objects:
        objects = ["inventory"]
        assumed = True
    action = "export_pptx"
    if "intake" in low:
        action = "item.intake"
    elif "agent.checked" in low or ("check" in low and "agent" in low):
        action = "agent.checked"
    verify = any(k in low for k in ("verify", "audit", "hypothes", "claim", "fact-check", "fact check"))
    agentish = any(k in low for k in ("single agent", "one agent", "worker loop"))
    foundryish = any(
        k in low for k in ("foundry", "create app", "pptx", "export", "ontology", "insight", "app")
    )
    if verify and not foundryish:
        pattern = "generator_verifier"
        kinds = ["connector", "hypothesize", "audit"]
    elif agentish and not foundryish:
        pattern = "single_agent"
        kinds = ["connector", "agent", "audit"]
    else:
        pattern = "orchestrator_subagent"
        kinds = ["connector", "ontology", "insight", "foundry", "app", "tool_call"]
    nodes: list[dict[str, Any]] = []
    for i, kind in enumerate(kinds):
        obj = objects[0]
        if kind == "ontology" and len(objects) > 1:
            obj = objects[1]
        node: dict[str, Any] = {
            "id": f"g{i + 1}",
            "kind": kind,
            "x": 32 + (i % 4) * 208,
            "y": 48 + (i // 4) * 160,
            "note": _KIND_NOTE.get(kind, kind),
            "tier": "T0",
            "stream": False,
        }
        if kind in ("connector", "ontology", "tool_call"):
            node["object_type"] = obj
            node["data_point"] = _DEFAULT_POINT.get(obj, "sku")
            node["data_type"] = "string"
            node["fetch_from"] = f"warehouse.{obj}"
        if kind == "tool_call":
            node["action_type"] = action
        elif kind == "foundry":
            node["action_type"] = action
        elif kind == "app":
            node["action_type"] = "emit"
        nodes.append(node)
    ids = [n["id"] for n in nodes]
    edges = [{"from": ids[i], "to": ids[i + 1]} for i in range(len(ids) - 1)]
    compile_constructor_graph({"nodes": nodes, "edges": edges})
    summary = (
        f"Compiled {len(nodes)} Cortex nodes ({pattern}). "
        + ("Assumed inventory. " if assumed else f"Objects {', '.join(objects)}. ")
        + f"Action {action}. Press a node for the decision layer."
    )
    return {
        "ok": True,
        "prompt": text,
        "assumed_object": assumed,
        "pattern": pattern,
        "action": action,
        "objects": objects,
        "nodes": nodes,
        "edges": edges,
        "summary": summary,
    }


def describe_constructor_node(payload: dict[str, Any], node_id: str | None) -> dict[str, Any]:
    """Cortex decision layer for one canvas node (compile truth, not a second engine)."""
    program = compile_constructor_graph(payload)
    raw_nodes = payload.get("nodes") or []
    target = node_id or program.entry_node_id
    raw = next((n for n in raw_nodes if isinstance(n, dict) and n.get("id") == target), None)
    dsl = next((n for n in program.nodes if n.id == target), None)
    if raw is None or dsl is None:
        raise ConstructorGraphError(f"node {target!r} not on this graph")
    kind = str(dsl.type.value if hasattr(dsl.type, "value") else dsl.type)
    write = raw.get("kind") in ("tool_call", "app")
    return {
        "ok": True,
        "node_id": target,
        "constructor_kind": raw.get("kind"),
        "cortex_kind": kind,
        "is_emit": target == program.output_node_id,
        "is_entry": target == program.entry_node_id,
        "inputs": list(dsl.inputs or []),
        "tier": str(raw.get("tier") or "T0"),
        "requires_confirm": raw.get("kind") == "tool_call",
        "would_write": write,
        "tool_name": getattr(dsl, "tool_name", None),
        "annotations": dsl.annotations or {},
        "entry_node_id": program.entry_node_id,
        "output_node_id": program.output_node_id,
        "engine": "cortex",
    }


def recommend_extras(kinds: list[str]) -> dict[str, Any] | None:
    """Signals so Cortex ranks the foundry path as orchestrator-subagent."""
    kindset = {str(k) for k in kinds}
    if {"ontology", "insight", "foundry", "app"} <= kindset:
        return {"specialization_needed": True, "prefer_cheapest": False}
    if {"hypothesize", "audit"} <= kindset:
        return {"quality_critical": True, "explicit_criteria": True}
    return None


def _envelope(program: AgenticDSLProgram) -> dict[str, Any]:
    nodes = []
    for node in program.nodes:
        item = node.model_dump(by_alias=True)
        kind = item.get("kind") or item.get("type")
        if hasattr(kind, "value"):
            item["kind"] = kind.value
        nodes.append(item)
    return {
        "version": program.version or "1.0",
        "entry_node_id": program.entry_node_id,
        "output_node_id": program.output_node_id,
        "nodes": nodes,
    }
