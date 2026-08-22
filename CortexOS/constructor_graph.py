"""Compile Constructor canvas JSON onto an existing AgenticDSL program."""

from __future__ import annotations

from typing import Any

from netie.fabrication.dsl_parser import AgenticDSLProgram, DSLNode, NodeType

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


class ConstructorGraphError(ValueError):
    pass


def compile_constructor_graph(payload: dict[str, Any]) -> AgenticDSLProgram:
    """Map {nodes, edges} to EMIT nodes. No new orchestrator. No LLM."""
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

    dsl_nodes: list[DSLNode] = []
    for raw in nodes:
        nid = raw["id"]
        dsl_nodes.append(
            DSLNode.model_validate(
                {
                    "id": nid,
                    "kind": NodeType.EMIT,
                    "inputs": inbound[nid],
                    "prompt": str(raw.get("note") or raw["kind"]),
                    "annotations": {
                        "constructor_kind": raw["kind"],
                        "object_type": raw.get("object_type"),
                        "action_type": raw.get("action_type"),
                    },
                }
            )
        )

    entry = nodes[0]["id"]
    output = nodes[-1]["id"]
    return AgenticDSLProgram(
        version="1.0",
        nodes=dsl_nodes,
        entry_node_id=entry,
        output_node_id=output,
    )
