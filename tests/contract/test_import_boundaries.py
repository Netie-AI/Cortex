"""AST-level import boundary enforcement for the contract package.

Why this file and not just ``lint-imports``: import-linter builds its graph
with grimp, which does **not** record function-level imports. Almost every
CortexOS -> packs crossing in this repo is a deferred import inside a function,
so import-linter reports the C2 contract KEPT while the crossings are still
there. This AST walk is the check that can actually see them.
"""
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


def _packs_crossings(path: Path) -> list[str]:
    return [mod for mod in _imports(path) if mod.startswith("packs.")]


def _crossings_under(base: Path, allowlist: set[str], rel_to: Path) -> list[str]:
    """Every ``packs.*`` import under ``base``, minus allowlisted exact paths."""
    offenders: list[str] = []
    for py in sorted(base.rglob("*.py")):
        rel = py.relative_to(rel_to).as_posix()
        if rel in allowlist:
            continue
        offenders.extend(f"{rel}:{mod}" for mod in _packs_crossings(py))
    return offenders


def test_contract_package_has_no_cortex_imports() -> None:
    """cortex_contract may only depend on pydantic and stdlib."""
    contract_root = ROOT / "packages" / "cortex_contract"
    for py in contract_root.glob("*.py"):
        for mod in _imports(py):
            assert not mod.startswith("CortexOS"), f"{py.name} imports {mod}"
            assert not mod.startswith("netie"), f"{py.name} imports {mod}"
            assert not mod.startswith("packs"), f"{py.name} imports {mod}"


# --- C2 evacuation debt ---
# EXACT repo-relative paths, never basenames. A basename allowlist silently
# shields every same-named file in the tree: the single entry "registry.py"
# covered both CortexOS/ontology/registry.py (which crosses) and
# CortexOS/engine/registry.py (which does not), so a new crossing added to the
# clean one would not have failed this test.
#
# This set may only SHRINK. Adding an entry is forbidden - declare a Protocol on
# the engine side and have the pack register an implementation instead, the way
# CortexOS/audit/ledger_registry.py does. Evacuating a file must delete its entry
# in the same commit; test_allowlist_entries_are_still_real_debt enforces that.
_C2_ALLOWLIST = {
    "CortexOS/api/a2a_routes.py",
    "CortexOS/api/action_routes.py",
    "CortexOS/api/agent_routes.py",
    "CortexOS/api/app.py",
    "CortexOS/api/brain_routes.py",
    "CortexOS/api/chat_routes.py",
    "CortexOS/api/context_routes.py",
    "CortexOS/api/discovery_routes.py",
    "CortexOS/api/engine_routes.py",
    "CortexOS/api/ingest_routes.py",
    "CortexOS/api/lakehouse_routes.py",
    "CortexOS/api/mcp_routes.py",
    "CortexOS/api/memory_routes.py",
    "CortexOS/api/pipeline_routes.py",
    "CortexOS/api/sidecar_routes.py",
    "CortexOS/api/skill_routes.py",
    "CortexOS/api/stream_routes.py",
    "CortexOS/api/task_routes.py",
    "CortexOS/api/telemetry_routes.py",
    "CortexOS/api/warehouse_routes.py",
    "CortexOS/dms/answer_engine.py",
    "CortexOS/dms/query_service.py",
    "CortexOS/dms/seed_demo.py",
    "CortexOS/execution/tool_runner.py",
    "CortexOS/nlp/local_inference.py",
    "CortexOS/ontology/registry.py",
    "CortexOS/ponytail/middleware.py",
}

# Evacuated through CortexOS/audit (#83). These must never come back.
_C2_EVACUATED = (
    "CortexOS/execution/goal_audit.py",
    "CortexOS/execution/dag_runner.py",
    "CortexOS/agent_sdk/sdk.py",
)


def test_cortexos_must_not_import_packs() -> None:
    """C2 boundary rule: CortexOS modules must not import from packs.*.

    The allowlist captures pre-C2 debt by exact path. Any other file that
    crosses - including a new file sharing an allowlisted basename - fails here.
    """
    offenders = _crossings_under(ROOT / "CortexOS", _C2_ALLOWLIST, ROOT)
    assert not offenders, (
        "Forbidden CortexOS -> packs imports (not in C2 allowlist):\n"
        + "\n".join(offenders)
    )


def test_c2_ledger_port_evacuated() -> None:
    """goal_audit / dag_runner / agent_sdk go through CortexOS.audit, not packs.*."""
    for rel in _C2_EVACUATED:
        assert rel not in _C2_ALLOWLIST, f"{rel} must not be re-allowlisted"
        packs = _packs_crossings(ROOT / rel)
        assert not packs, f"{rel} still imports {packs}"


def test_allowlist_entries_are_still_real_debt() -> None:
    """The list must describe the debt, not outlive it.

    A stale entry is a silent hole: it keeps shielding a path after the crossing
    is gone, so a later re-introduction would not fail. Evacuate a file, delete
    its line.
    """
    stale: list[str] = []
    for rel in sorted(_C2_ALLOWLIST):
        path = ROOT / rel
        if not path.is_file():
            stale.append(f"{rel} (no such file)")
        elif not _packs_crossings(path):
            stale.append(f"{rel} (no longer imports packs.* - delete this entry)")
    assert not stale, "Stale C2 allowlist entries:\n" + "\n".join(stale)


def test_the_gate_catches_a_new_crossing(tmp_path: Path) -> None:
    """R-0007: a gate nobody has watched fail is not a gate.

    Two shapes must be caught: a brand-new crossing, and a crossing in a file
    whose BASENAME is allowlisted but whose path is not - the exact hole the
    old basename allowlist left open.
    """
    engine = tmp_path / "CortexOS"
    (engine / "api").mkdir(parents=True)
    (engine / "engine").mkdir(parents=True)

    (engine / "api" / "app.py").write_text(
        "from packs.dms.thing import x\n", encoding="utf-8"
    )  # allowlisted by exact path -> must be ignored
    (engine / "api" / "brand_new_routes.py").write_text(
        "from packs.dms.other import y\n", encoding="utf-8"
    )  # never allowlisted -> must be caught
    (engine / "engine" / "app.py").write_text(
        "def f():\n    from packs.dms.sneaky import z\n", encoding="utf-8"
    )  # basename twin, function-level -> must be caught

    offenders = _crossings_under(engine, _C2_ALLOWLIST, tmp_path)

    assert any("api/brand_new_routes.py" in o for o in offenders), offenders
    assert any("engine/app.py" in o for o in offenders), (
        "a same-basename twin must not inherit an allowlist entry",
        offenders,
    )
    assert not any(o.startswith("CortexOS/api/app.py") for o in offenders), offenders
