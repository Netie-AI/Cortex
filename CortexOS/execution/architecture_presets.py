"""Architecture presets — MoE picker catalog (PRODUCT_ROLES: Cortex owns this).

Does not execute a new orchestrator. Maps preset id → existing Cortex paths
(dag_runner, memory/RAG assemble, marketplace adapters behind the Cortex gate).
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


class PresetUnavailable(RuntimeError):
    """The preset is declared but its runner has no implementation."""


def runner_available(runner: str) -> bool:
    """Can ``execute_run_plan`` actually run this? Derived, never hand-kept.

    Imported lazily: ``run_plan`` pulls in the agentic extras, and merely asking
    which architectures exist must not require them.
    """
    from CortexOS.execution.run_plan import IMPLEMENTED_RUNNERS

    return runner in IMPLEMENTED_RUNNERS


def available_presets() -> list[str]:
    """Preset ids the engine can execute today."""
    return [p["id"] for p in PRESET_CATALOG if runner_available(str(p["runner"]))]


def catalog() -> list[dict[str, Any]]:
    """The catalog, each entry stating whether it can actually run.

    ``langgraph`` and ``langchain`` resolve to permanent 501 stubs. Advertising
    them without this flag let a routine pin itself to one and fail on every
    single run — 327 consecutive times on this machine — discovering each time
    something knowable before the first.
    """
    return [
        {**p, "available": runner_available(str(p["runner"]))} for p in PRESET_CATALOG
    ]


def resolve_runner(preset: str | None, *, require_available: bool = False) -> dict[str, Any]:
    """Map preset → RunPlan stub for existing runners (no new loop).

    ``require_available`` refuses a preset whose adapter was never written,
    once and with a reason, instead of returning a plan that is certain to
    fail. Callers that schedule or race work should pass it; callers that
    merely describe the catalog should not.
    """
    pid = normalize_preset(preset)
    meta = next(p for p in PRESET_CATALOG if p["id"] == pid)
    runner = str(meta["runner"])
    if require_available and not runner_available(runner):
        raise PresetUnavailable(
            f"architecture preset {pid!r} maps to runner {runner!r}, whose adapter "
            "is not implemented — it cannot succeed, so it is refused here rather "
            "than failing on every run. Pick an available preset "
            f"({', '.join(available_presets())}) or implement the adapter."
        )
    return {
        "preset": pid,
        "runner": runner,
        "name": meta["name"],
        "available": runner_available(runner),
        "openvault_gate_required": True,
        "note": "Dispatch enters existing dag_runner / compose paths — never a third orchestrator.",
    }
