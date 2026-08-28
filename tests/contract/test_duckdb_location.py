"""C4 — DuckDB may only be imported under CortexOS/execution/.

The enforcer lives there; every other CortexOS module must go through the
execution warehouse / submit ports. packs/ and scripts/ lakehouse opens are
tracked as C4.follow and are out of this CortexOS-scoped gate.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORTEX_OS = ROOT / "CortexOS"
ALLOWED_ROOT = CORTEX_OS / "execution"


def _imports_duckdb(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "duckdb" or alias.name.startswith("duckdb.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "duckdb" or node.module.startswith("duckdb.")):
                return True
    return False


def test_no_duckdb_import_outside_execution() -> None:
    offenders: list[str] = []
    for py in CORTEX_OS.rglob("*.py"):
        if ALLOWED_ROOT in py.parents or py.parent == ALLOWED_ROOT:
            continue
        if _imports_duckdb(py):
            offenders.append(str(py.relative_to(ROOT)).replace("\\", "/"))
    assert offenders == [], (
        "duckdb import outside CortexOS/execution/ (C4):\n  " + "\n  ".join(offenders)
    )


def test_execution_warehouse_is_the_open_site() -> None:
    """Smoke: the designated module still imports duckdb (so the invariant is meaningful)."""
    warehouse = ALLOWED_ROOT / "warehouse.py"
    assert warehouse.is_file()
    assert _imports_duckdb(warehouse)
