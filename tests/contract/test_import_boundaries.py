"""AST-level import boundary enforcement for the contract package."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> list[str]:
    out: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def test_contract_package_has_no_cortex_imports() -> None:
    """cortex_contract may only depend on pydantic and stdlib."""
    contract_root = ROOT / "packages" / "cortex_contract"
    for py in contract_root.glob("*.py"):
        for mod in _imports(py):
            assert not mod.startswith("CortexOS"), f"{py.name} imports {mod}"
            assert not mod.startswith("netie"), f"{py.name} imports {mod}"
            assert not mod.startswith("packs"), f"{py.name} imports {mod}"


# --- C2 evacuation debt ---
# These files currently cross the CortexOS -> packs boundary.
# Each must be resolved during C2 evacuation; new additions fail the test.
_C2_ALLOWLIST = {
    "engine_routes.py",
    "chat_routes.py",
    "skill_routes.py",
    "task_routes.py",
    "agent_routes.py",
    "brain_routes.py",
    "ingest_routes.py",
    "lakehouse_routes.py",
    "memory_routes.py",
    "pipeline_routes.py",
    "stream_routes.py",
    "warehouse_routes.py",
    "action_routes.py",
    "sidecar_routes.py",
    "a2a_routes.py",
    "mcp_routes.py",
    "discovery_routes.py",
    "app.py",
    "telemetry_routes.py",
    "context_routes.py",
    "seed_demo.py",
    "query_service.py",
    "answer_engine.py",
    "tool_runner.py",
    "dag_runner.py",
    "goal_audit.py",
    "local_inference.py",
    "middleware.py",
    "registry.py",  # CortexOS/ontology/registry.py
    "sdk.py",  # CortexOS/agent_sdk/sdk.py
}


def test_cortexos_must_not_import_packs() -> None:
    """C2 boundary rule: CortexOS modules must not import from packs.*.

    Allowlist captures pre-C2 debt. New files that cross the boundary
    will fail this test immediately.
    """
    offenders: list[str] = []
    for py in (ROOT / "CortexOS").rglob("*.py"):
        if py.name in _C2_ALLOWLIST:
            continue
        for mod in _imports(py):
            if mod.startswith("packs."):
                offenders.append(f"{py.relative_to(ROOT)}:{mod}")
    assert not offenders, (
        "Forbidden CortexOS -> packs imports (not in C2 allowlist):\n"
        + "\n".join(offenders)
    )
