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
        tier = str(raw.get("tier") or "T0").upper()
        if tier not in ("T0", "T1"):
            tier = "T0"
        if ntype in (NodeType.LLM_JUDGED, NodeType.AGENT_TASK, NodeType.RAG_ANSWER, NodeType.TOOL_CALL):
            fields["default_tier"] = tier
            fields["max_tier"] = "T1"
        if ntype == NodeType.TOOL_CALL:
            fields["tool_name"] = str(raw.get("action_type") or "export_pptx")
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
