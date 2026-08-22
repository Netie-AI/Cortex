"""Cortex#14 — serving DuckDB path is one env both products honor.

TAS-DMS measured Studio writing <DMS>/data/dms_demo.duckdb while Cortex
answered <Cortex>/data/dms_demo.duckdb because DEFAULT_DB was snapshotted
at import. These tests fail if reader and writer resolve different files,
or if a second CortexOS default reappears.
"""

from __future__ import annotations

import ast
from pathlib import Path

from CortexOS.execution.warehouse import (
    DEFAULT_DB,
    FALLBACK_DB,
    WAREHOUSE_DB_ENV,
    connect_write,
    get_connection,
    warehouse_path,
)
from CortexOS.paths import data_path, repo_root

ROOT = Path(__file__).resolve().parents[2]
CORTEX_OS = ROOT / "CortexOS"


def test_shared_env_name_is_dms_warehouse_db() -> None:
    """DMS Studio ingest and Cortex answers pin this exact name (C4 packet)."""
    assert WAREHOUSE_DB_ENV == "DMS_WAREHOUSE_DB"


def test_unset_env_keeps_documented_demo_grant() -> None:
    """Unbound /dms/query demo revenue: fallback is the in-repo demo file."""
    assert FALLBACK_DB == data_path("dms_demo.duckdb")
    assert FALLBACK_DB == repo_root() / "data" / "dms_demo.duckdb"
    assert DEFAULT_DB == FALLBACK_DB


def test_warehouse_path_honors_env_after_import(monkeypatch, tmp_path) -> None:
    shared = tmp_path / "studio.duckdb"
    monkeypatch.setenv(WAREHOUSE_DB_ENV, str(shared))
    assert warehouse_path() == shared
    # Import-time DEFAULT_DB used to freeze the Cortex-repo file; passing it
    # must still re-read the env so get_connection(DEFAULT_DB) is not a miss.
    assert warehouse_path(DEFAULT_DB) == shared
    assert warehouse_path(FALLBACK_DB) == shared


def test_reader_and_writer_open_the_same_file(monkeypatch, tmp_path) -> None:
    shared = tmp_path / "shared.duckdb"
    monkeypatch.setenv(WAREHOUSE_DB_ENV, str(shared))

    writer = connect_write()
    try:
        writer.execute("CREATE TABLE path_probe (id INTEGER)")
        writer.execute("INSERT INTO path_probe VALUES (14)")
    finally:
        writer.close()

    assert shared.is_file()
    assert warehouse_path() == shared

    reader = get_connection(DEFAULT_DB, read_only=True)
    try:
        n = reader.execute("SELECT COUNT(*) FROM path_probe").fetchone()[0]
        assert int(n) == 1
    finally:
        reader.close()


def test_explicit_tmp_path_is_not_hijacked_by_env(monkeypatch, tmp_path) -> None:
    """Tests that pass a tmp warehouse must not silently follow the env."""
    env_db = tmp_path / "from_env.duckdb"
    explicit = tmp_path / "explicit.duckdb"
    monkeypatch.setenv(WAREHOUSE_DB_ENV, str(env_db))
    assert warehouse_path(explicit) == explicit


def test_no_second_hardcoded_serving_duckdb_in_cortexos() -> None:
    """Fails if another CortexOS module grows its own dms_demo.duckdb default."""
    allow = {"warehouse.py"}
    offenders: list[str] = []
    for py in CORTEX_OS.rglob("*.py"):
        if py.name in allow:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "dms_demo.duckdb":
                offenders.append(str(py.relative_to(ROOT)).replace("\\", "/"))
                break
    assert offenders == [], (
        "second serving-DuckDB default (must go through warehouse_path / "
        f"{WAREHOUSE_DB_ENV}):\n  " + "\n  ".join(offenders)
    )
