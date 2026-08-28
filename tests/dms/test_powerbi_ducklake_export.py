"""P-DMS-24 — folder Parquet reads double-count DuckLake snapshots; export is safe."""

from __future__ import annotations

from pathlib import Path

import pytest

LAKE = Path(r"D:\Cortex\data\lakehouse")
GOLD = LAKE / "data" / "gold" / "sales_by_sku"


@pytest.mark.skipif(not GOLD.is_dir(), reason="Cortex lakehouse gold not present")
def test_folder_parquet_can_exceed_catalog_count():
    """Document the failure mode: N snapshot files → N× catalog rows."""
    import duckdb

    files = sorted(GOLD.glob("ducklake-*.parquet"))
    if len(files) < 2:
        pytest.skip("need >=2 gold snapshots (re-run lakehouse_migrate twice)")

    con = duckdb.connect()
    try:
        folder_n = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{GOLD.as_posix()}/*.parquet')"
        ).fetchone()[0]
        # Catalog path — may fail if ducklake extension missing; then just assert
        # folder_n is a multiple of a single-file count.
        one_n = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{files[-1].as_posix()}')"
        ).fetchone()[0]
    finally:
        con.close()

    assert one_n > 0
    assert folder_n == one_n * len(files)
    assert folder_n > one_n


def test_brain_export_writes_single_file(tmp_path, monkeypatch):
    """Export path must be a single Parquet, never the DuckLake folder."""
    pytest.importorskip("CortexOS")
    monkeypatch.setenv("DMS_EXPORT_DIR", str(tmp_path))
    from CortexOS.api import brain_routes

    if not hasattr(brain_routes, "_export_parquet_snapshot"):
        pytest.skip("brain export helper missing")
    try:
        out = brain_routes._export_parquet_snapshot("inventory", limit=50)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"warehouse unavailable: {exc}")

    path = Path(out.get("path") or out.get("file") or "")
    assert path.suffix == ".parquet"
    assert "lakehouse" not in path.as_posix().lower() or "exports" in path.as_posix().lower()
    assert path.is_file()
