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
        "enhance",
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
    "enhance": NodeType.DOCUMENT_REF,
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


FOUNDRY_KINDS = frozenset({"ontology", "insight", "foundry", "app"})

# Palantir-shaped generate intents. Each seed keeps ontology+insight+foundry+app
# even when the prompt sounds like "agents" or "data" alone.
NAMED_GENERATE = (
    "understand this company",
    "define data",
    "govern agents",
    "business insights",
)

# DMS semantic_layer.yaml tables only. No extra object without a matching table.
_OBJECT_POINTS: dict[str, tuple[str, str]] = {
    "inventory": ("sku", "warehouse.inventory"),
    "suppliers": ("supplier_id", "warehouse.suppliers"),
    "locations": ("location_id", "warehouse.locations"),
    "shipments": ("shipment_id", "warehouse.shipments"),
    "transactions": ("txn_id", "warehouse.transactions"),
    "alerts": ("alert_id", "warehouse.alerts"),
}

_REFUSE_HITS = (
    "prostitut",
    "escort",
    "brothel",
    "sex work",
    "sexworker",
    "stalk",
    "doxx",
    "scrape the internet",
    "scrape internet",
    "public webcam",
    "scrape camera",
)

_KIND_NOTES = {
    "ingest": "Hop 0. Load semantic_layer rows. Ghost compiles. No write.",
    "connector": "First-party Cortex input. No n8n.",
    "ontology": "Object/link/action types transcribed from semantic_layer.yaml.",
    "insight": "Cite ontology + ledger. What you may claim from those objects.",
    "foundry": "Compile insights into a governed Cortex app. Not an n8n clone.",
    "app": "Runnable output. Hosted inside Cortex at /cortex/constructor/.",
    "agent": "AGENT_TASK loop. Action types + required_role. One bounded worker.",
    "tool_call": "F8 governed write. requires_confirm. Real tool is export_pptx.",
    "hypothesize": "Surface a testable claim.",
    "audit": "Why this node exists. DETERMINISTIC_RULE, not a second EMIT.",
}


def recommend_extras(kinds: list[str]) -> dict[str, Any] | None:
    """Signals so Cortex ranks the foundry path as orchestrator-subagent."""
    kindset = {str(k) for k in kinds}
    if FOUNDRY_KINDS <= kindset:
        return {"specialization_needed": True, "prefer_cheapest": False}
    if {"hypothesize", "audit"} <= kindset:
        return {"quality_critical": True, "explicit_criteria": True}
    return None


def generate_constructor_graph(prompt: str) -> dict[str, Any]:
    """Compile a prompt onto a Constructor canvas. Ghost only. No writes.

    Named seeds (understand this company / define data / govern agents /
    business insights) always include ontology+insight+foundry+app. Object
    types are semantic_layer tables only.
    """
    text = str(prompt or "").strip()
    if not text:
        raise ConstructorGraphError("prompt required")
    low = " ".join(text.lower().split())
    if any(hit in low for hit in _REFUSE_HITS):
        return {
            "ok": False,
            "refused": True,
            "ghost": True,
            "nodes": [],
            "edges": [],
            "summary": (
                "Refused. Constructor will not compile internet stalking, "
                "doxxing, public-webcam scrape, or sex-work targeting."
            ),
        }

    spec = _named_spec(low) or _default_spec(low)
    nodes, edges = _seed_graph(spec)
    compile_constructor_graph({"nodes": nodes, "edges": edges})
    assumed = bool(spec["assumed"])
    objects = list(spec["objects"])
    action = str(spec["action"])
    return {
        "ok": True,
        "ghost": True,
        "pattern": spec["pattern"],
        "assumed_object": assumed,
        "objects": objects,
        "action": action,
        "nodes": nodes,
        "edges": edges,
        "summary": (
            f"Compiled {len(nodes)} Cortex nodes ({spec['pattern']}). "
            + ("Assumed inventory. " if assumed else f"Objects {', '.join(objects)}. ")
            + f"Action {action}. Ghost only. Live run is POST /cortex/constructor/run."
        ),
    }


def _named_spec(low: str) -> dict[str, Any] | None:
    if "understand this company" in low:
        return {
            "kinds": ("connector", "ontology", "insight", "foundry", "app"),
            "objects": ("inventory", "suppliers", "locations"),
            "action": "export_pptx",
            "pattern": "orchestrator_subagent",
            "assumed": False,
            "notes": {
                "connector": "Bind warehouse rows that describe this company.",
                "ontology": "Company model = object/link/action types already on the pack.",
                "insight": "Cite ontology + ledger. What this company may claim.",
                "foundry": "Compile the company model into a governed Cortex app.",
                "app": "Runnable output hosted at /cortex/constructor/.",
            },
        }
    if "define data" in low:
        return {
            "kinds": ("ingest", "connector", "ontology", "insight", "foundry", "app"),
            "objects": ("inventory",),
            "action": "export_pptx",
            "pattern": "orchestrator_subagent",
            "assumed": False,
            "notes": {
                "ingest": "Hop 0. Load semantic_layer columns as properties. No write.",
                "connector": "Bind warehouse.inventory. Data points are table columns.",
                "ontology": "Properties must match semantic_layer.yaml columns exactly.",
                "insight": "Cite defined properties + joins. No extra object types.",
                "foundry": "Compile the defined data into a governed Cortex app.",
                "app": "Runnable output hosted at /cortex/constructor/.",
            },
        }
    if "govern agents" in low:
        return {
            "kinds": ("connector", "ontology", "agent", "insight", "foundry", "app"),
            "objects": ("inventory",),
            "action": "agent.checked",
            "pattern": "orchestrator_subagent",
            "assumed": False,
            "notes": {
                "connector": "Bind the objects an agent may read.",
                "ontology": "Action types + required_role are the agent permission gate.",
                "agent": "AGENT_TASK. agent.checked is a ledger event, not an F8 tool.",
                "insight": "Cite what the agent may claim after a governed check.",
                "foundry": "Compile governed agents into a Cortex app. Not AIP Agent Studio.",
                "app": "Runnable output hosted at /cortex/constructor/.",
            },
        }
    if "business insights" in low:
        return {
            "kinds": ("connector", "ontology", "insight", "foundry", "app", "tool_call"),
            "objects": ("inventory", "alerts"),
            "action": "export_pptx",
            "pattern": "orchestrator_subagent",
            "assumed": False,
            "notes": {
                "connector": "Bind inventory + alerts the insight may cite.",
                "ontology": "Insights ride object types, not a second metrics store.",
                "insight": "Cite ontology + ledger. Business claim from owned rows.",
                "foundry": "Compile insights into a governed Cortex app.",
                "app": "Runnable output hosted at /cortex/constructor/.",
                "tool_call": "F8 governed write. requires_confirm. export_pptx only.",
            },
        }
    return None


def _default_spec(low: str) -> dict[str, Any]:
    objects = tuple(oid for oid in _OBJECT_POINTS if oid in low or oid.rstrip("s") in low)
    assumed = not objects
    if assumed:
        objects = ("inventory",)
    action = "export_pptx"
    if "intake" in low:
        action = "item.intake"
    elif "agent.checked" in low or ("agent" in low and "check" in low):
        action = "agent.checked"
    kinds: tuple[str, ...] = (
        "ingest",
        "connector",
        "ontology",
        "insight",
        "foundry",
        "app",
        "tool_call",
    )
    return {
        "kinds": kinds,
        "objects": objects,
        "action": action,
        "pattern": "orchestrator_subagent",
        "assumed": assumed,
        "notes": {},
    }


def _seed_graph(spec: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    kinds: tuple[str, ...] = spec["kinds"]
    objects: tuple[str, ...] = spec["objects"]
    action = str(spec["action"])
    notes: dict[str, str] = spec.get("notes") or {}
    bind = frozenset(
        {"ingest", "connector", "ontology", "insight", "enhance", "tool_call"}
    )
    nodes: list[dict[str, Any]] = []
    for i, kind in enumerate(kinds):
        obj = objects[0]
        if kind == "ontology" and len(objects) > 1:
            obj = objects[1]
        elif kind == "insight" and len(objects) > 2:
            obj = objects[2]
        elif kind in {"tool_call", "app"}:
            obj = objects[-1]
        point, place = _OBJECT_POINTS[obj]
        node: dict[str, Any] = {
            "id": f"g{i + 1}",
            "kind": kind,
            "x": 32 + (i % 4) * 208,
            "y": 48 + (i // 4) * 168,
            "note": notes.get(kind) or _KIND_NOTES.get(kind) or kind,
            "tier": "T0",
            "stream": False,
        }
        if kind in bind:
            node["object_type"] = obj
            node["data_point"] = point
            node["data_type"] = "string"
            node["fetch_from"] = place
        if kind in {"tool_call", "foundry"}:
            node["action_type"] = action
        if kind == "agent":
            node["action_type"] = action if action.startswith("agent.") else "agent.checked"
        if kind == "app":
            node["action_type"] = "emit"
        nodes.append(node)
    edges = [{"from": nodes[i]["id"], "to": nodes[i + 1]["id"]} for i in range(len(nodes) - 1)]
    return nodes, edges


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
