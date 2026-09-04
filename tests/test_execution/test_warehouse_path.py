"""warehouse_path re-reads DMS_WAREHOUSE_DB at call time."""

from __future__ import annotations

from CortexOS.execution import warehouse as w


def test_warehouse_path_honors_env_after_import(tmp_path, monkeypatch):
    frozen = w.DEFAULT_DB
    isolated = tmp_path / "call_time.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(isolated))
    assert w.DEFAULT_DB == frozen
    assert w.warehouse_path() == isolated
    assert w.warehouse_path() != frozen
