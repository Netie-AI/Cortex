"""L0 lakehouse smoke tests — run in BOTH ducklake and fallback modes.

The suite parametrizes over mode by forcing fallback via env for one pass and
letting the real capability drive the other, so CI on an air-gapped box (no
ducklake extension) still exercises the whole API surface honestly.
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def lake_home(tmp_path, monkeypatch):
    home = tmp_path / "lakehouse"
    monkeypatch.setenv("DMS_LAKEHOUSE_HOME", str(home))
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    # Own warehouse, not the shared serving one. `sync_warehouse_from_silver`
    # is a writer, and DuckDB gives a writer the file exclusively — so pointed
    # at data/dms_demo.duckdb these tests fail whenever an engine is running,
    # which reads as flakiness and is really contention. This is the durable
    # fix STATUS.md recorded as unclaimed on 2026-07-27.
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "demo.duckdb"))
    # Fresh capability probe per test.
    from packs.dms.lakehouse import catalog

    catalog.reset_mode_cache()
    yield home
    catalog.reset_mode_cache()


@pytest.fixture(params=["auto", "fallback"])
def mode_env(request, monkeypatch):
    from packs.dms.lakehouse import catalog

    if request.param == "fallback":
        monkeypatch.setenv("DMS_LAKEHOUSE_FORCE_FALLBACK", "1")
    else:
        monkeypatch.delenv("DMS_LAKEHOUSE_FORCE_FALLBACK", raising=False)
    catalog.reset_mode_cache()
    return request.param


def test_attach_and_schemas(lake_home, mode_env):
    from packs.dms.lakehouse.catalog import SCHEMAS, connect

    con = connect()
    try:
        found = {
            r[0] for r in con.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE catalog_name = 'lake'").fetchall()
        }
    finally:
        con.close()
    assert set(SCHEMAS).issubset(found)


def test_write_read_roundtrip(lake_home, mode_env):
    from packs.dms.lakehouse import tables as lt

    n = lt.write_table("silver", "t", rows=[{"id": 1, "q": 10.0}, {"id": 2, "q": 20.0}])
    assert n == 2
    rows = lt.read("silver", "t")
    assert sorted(r["id"] for r in rows) == [1, 2]
    lt.append_rows("silver", "t", [{"id": 3, "q": 30.0}])
    assert len(lt.read("silver", "t")) == 3


def test_snapshots_and_time_travel(lake_home, mode_env):
    from packs.dms.lakehouse import tables as lt
    from packs.dms.lakehouse.catalog import connect, lakehouse_mode

    lt.write_table("silver", "t", rows=[{"id": 1, "q": 10.0}])
    if lakehouse_mode() != "ducklake":
        assert lt.snapshots("silver", "t") == []
        with pytest.raises(lt.LakehouseError):
            lt.query_at(0, "silver", "t")
        return

    con = connect()
    con.execute("UPDATE lake.silver.t SET q = 999 WHERE id = 1")
    con.close()
    snaps = lt.snapshots("silver", "t")
    assert len(snaps) >= 2
    v_before = snaps[-2].snapshot_id
    assert lt.read("silver", "t")[0]["q"] == 999.0
    assert lt.query_at(v_before, "silver", "t", columns="q", where="id=1")[0]["q"] == 10.0


def test_schema_evolution_guard(lake_home, mode_env):
    from packs.dms.lakehouse import tables as lt

    lt.write_table("silver", "t", rows=[{"id": 1, "q": 10.0}])
    lt.add_column("silver", "t", "note", "VARCHAR")
    assert "note" in lt.read("silver", "t", limit=1)[0]
    with pytest.raises(lt.LakehouseError):
        lt.drop_column("silver", "t", "note")  # destructive without force
    lt.drop_column("silver", "t", "note", force=True)
    assert "note" not in lt.read("silver", "t", limit=1)[0]


def test_identifier_and_schema_guards(lake_home, mode_env):
    from packs.dms.lakehouse import tables as lt

    with pytest.raises(lt.LakehouseError):
        lt.write_table("purple", "x", rows=[{"a": 1}])
    with pytest.raises(lt.LakehouseError):
        lt.read("silver", "x; DROP TABLE y")


def test_migration_seeds_all_tables(lake_home, mode_env):
    from scripts.lakehouse_migrate import migrate_all
    from packs.dms.lakehouse import tables as lt

    result = migrate_all()
    assert result["mode"] in ("ducklake", "fallback")
    listed = lt.list_tables()
    for t in ("inventory", "suppliers", "locations", "shipments", "transactions", "alerts"):
        assert t in listed["silver"], t
        assert f"{t}_raw" in listed["bronze"], t
    assert {"sales_by_sku", "capacity_by_location", "supplier_risk"} <= set(listed["gold"])
    # silver typing survived (numeric aggregate works)
    total = lt.read("gold", "sales_by_sku")
    assert total and any(r.get("sales_value_myr") for r in total)


def test_status_reports_mode(lake_home, mode_env):
    from scripts.lakehouse_migrate import migrate_all
    from packs.dms.lakehouse.catalog import lakehouse_status, lakehouse_mode

    migrate_all()
    st = lakehouse_status()
    assert st["lakehouse_mode"] == lakehouse_mode()
    assert st["time_travel"] == (lakehouse_mode() == "ducklake")
    assert "silver" in st["schemas"] and len(st["schemas"]["silver"]) == 6
