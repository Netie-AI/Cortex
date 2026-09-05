"""Architecture presets — MoE picker catalog (PRODUCT_ROLES: Cortex owns this).

Does not execute a new orchestrator. Maps preset id → existing Cortex paths
(dag_runner, memory/RAG assemble, marketplace adapters behind the Cortex gate).

Constructor distill-options (myn8n / langchain / langflow / gencfsm_dag) are
not product presets. See ``CortexOS.execution.distill_options`` — route table
only, engine_role never product_engine.
"""

from __future__ import annotations

from typing import Any, Literal

ArchitecturePreset = Literal[
    "dag",
    "sequential",
    "langgraph",
    "langchain",
    "minimal",
    "rag",
    "memory",
    "computer_control",
]

DEFAULT_PRESET: ArchitecturePreset = "minimal"

PRESET_CATALOG: list[dict[str, Any]] = [
    {
        "id": "minimal",
        "name": "Minimal",
        "default": True,
        "blurb": "Single-node / short tool loop — cheapest default.",
        "runner": "dag_single",
    },
    {
        "id": "sequential",
        "name": "Sequential",
        "default": False,
        "blurb": "Linear steps as a degenerate DAG.",
        "runner": "dag_linear",
    },
    {
        "id": "dag",
        "name": "DAG",
        "default": False,
        "blurb": "Full governed DAG via dag_runner + cost ledger.",
        "runner": "dag_runner",
    },
    {
        "id": "langgraph",
        "name": "LangGraph-style",
        "default": False,
        "blurb": "Graph agent runtime — marketplace adapter, still behind Cortex gate.",
        "runner": "marketplace_langgraph",
    },
    {
        "id": "langchain",
        "name": "LangChain-style",
        "default": False,
        "blurb": "Chain/tool runtime — marketplace adapter, behind Cortex gate.",
        "runner": "marketplace_langchain",
    },
    {
        "id": "rag",
        "name": "RAG",
        "default": False,
        "blurb": "Retrieve-then-answer via rag/* + context_engineering assemble.",
        "runner": "rag_compose",
    },
    {
        "id": "memory",
        "name": "Memory",
        "default": False,
        "blurb": "Memory-plane first (/api/memory) then answer.",
        "runner": "memory_compose",
    },
    {
        "id": "computer_control",
        "name": "Computer control",
        "default": False,
        "blurb": "Tool/action path via ontology call_action — not a separate runtime.",
        "runner": "ontology_actions",
    },
]

_VALID = {p["id"] for p in PRESET_CATALOG}


def normalize_preset(value: str | None) -> ArchitecturePreset:
    v = (value or DEFAULT_PRESET).strip().lower().replace("-", "_")
    if v == "langgraph_style":
        v = "langgraph"
    if v not in _VALID:
        return DEFAULT_PRESET
    return v  # type: ignore[return-value]


def catalog() -> list[dict[str, Any]]:
    return list(PRESET_CATALOG)


def resolve_runner(preset: str | None) -> dict[str, Any]:
    """Map preset → RunPlan stub for existing runners (no new loop)."""
    pid = normalize_preset(preset)
    meta = next(p for p in PRESET_CATALOG if p["id"] == pid)
    return {
        "preset": pid,
        "runner": meta["runner"],
        "name": meta["name"],
        "openvault_gate_required": True,
        "note": "Dispatch enters existing dag_runner / compose paths — never a third orchestrator.",
    }
