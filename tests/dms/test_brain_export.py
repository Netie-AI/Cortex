"""Brain export route — allowlisted table CSV / parquet snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DMS_EXPORT_DIR", str(tmp_path / "exports"))
    # Steward role key used by demo auth — skip if auth fixture differs.
    from CortexOS.api.app import app

    return TestClient(app)


def test_get_db_data_inventory_not_empty():
    from CortexOS.api.brain_routes import _get_db_data

    rows = _get_db_data("inventory", limit=10)
    assert len(rows) >= 1
    assert "sku" in rows[0]


def test_get_db_data_alias_dms_inventory():
    from CortexOS.api.brain_routes import _get_db_data

    rows = _get_db_data("dms_inventory", limit=5)
    assert len(rows) >= 1


def test_get_db_data_unknown_table():
    from fastapi import HTTPException

    from CortexOS.api.brain_routes import _get_db_data

    with pytest.raises(HTTPException):
        _get_db_data("not_a_real_table", limit=5)


def test_parquet_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DMS_EXPORT_DIR", str(tmp_path / "exports"))
    from CortexOS.api.brain_routes import _export_parquet_snapshot

    result = _export_parquet_snapshot("inventory", limit=25)
    assert result["format"] == "parquet"
    assert result["row_count"] >= 1
    assert Path(result["path"]).is_file()
    assert "DuckLake" in result["summary"] or "Parquet" in result["summary"]
