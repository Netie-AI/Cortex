"""L2 — declarative pipelines + expectations (warn/drop/fail), quarantine,
event log, and approval-gated proposals."""
from __future__ import annotations

import pytest


@pytest.fixture
def lake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_LAKEHOUSE_HOME", str(tmp_path / "lakehouse"))
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("DMS_PROPOSED_DIR", str(tmp_path / "proposed"))
    # Own warehouse — the promote path writes, and a writer needs the DuckDB
    # file exclusively, so sharing data/dms_demo.duckdb makes these tests fail
    # whenever an engine holds it.
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "demo.duckdb"))
    from packs.dms.lakehouse import catalog

    catalog.reset_mode_cache()
    yield tmp_path
    catalog.reset_mode_cache()


def _seed_raw(con):
    con.execute(
        "CREATE OR REPLACE TABLE lake.bronze.raw AS SELECT * FROM (VALUES "
        "('A',' 10 ','0.5'),('B','-5','0.9'),('C',NULL,'1.5'),('D','20','0.2')"
        ") t(sku, qty_txt, risk_txt)")


_DEF = {
    "id": "raw_to_silver",
    "source": "bronze.raw",
    "target": "silver.clean",
    "lineage": "aggregate",
    "lineage_reason": "test fixture drops provenance; aggregate waiver documented",
    "transform_sql": "SELECT sku, TRY_CAST(TRIM(qty_txt) AS DOUBLE) AS qty, "
                     "TRY_CAST(risk_txt AS DOUBLE) AS risk FROM {source}",
    "expectations": [
        {"name": "qty_non_negative", "constraint_sql": "qty >= 0", "action": "drop"},
        {"name": "sku_present", "constraint_sql": "sku IS NOT NULL", "action": "drop"},
        {"name": "risk_in_range", "constraint_sql": "risk BETWEEN 0 AND 1", "action": "warn"},
    ],
}


def test_expectations_warn_drop_and_reconcile(lake_home):
    from packs.dms.lakehouse.catalog import connect
    from packs.dms.pipelines import runner

    con = connect()
    _seed_raw(con)
    con.close()
    run = runner.run_pipeline(_DEF)
    assert run.status == "completed"
    assert run.rows_in == 4 and run.rows_out == 2 and run.rows_dropped == 2
    assert run.rows_in == run.rows_out + run.rows_dropped  # reconciliation


def test_quarantine_reasons(lake_home):
    from packs.dms.lakehouse.catalog import connect
    from packs.dms.lakehouse import tables as lt
    from packs.dms.pipelines import runner

    con = connect()
    _seed_raw(con)
    con.close()
    runner.run_pipeline(_DEF)
    q = {r["sku"]: r["_quarantine_reason"] for r in lt.read("silver", "clean_quarantine")}
    assert q.get("B") == "qty_non_negative"      # -5 < 0
    assert q.get("C") == "qty_non_negative"      # NULL qty fails the check


def test_fail_expectation_aborts_without_writing_target(lake_home):
    from packs.dms.lakehouse.catalog import connect
    from packs.dms.lakehouse import tables as lt
    from packs.dms.pipelines import runner

    con = connect()
    _seed_raw(con)
    con.close()
    pdef = dict(_DEF, id="raw_fail", target="silver.clean2",
                expectations=[{"name": "no_null_qty",
                               "constraint_sql": "TRY_CAST(TRIM(qty_txt) AS DOUBLE) IS NOT NULL",
                               "action": "fail"}])
    # transform aliases qty, so express the fail on the transformed column
    pdef["expectations"] = [{"name": "no_null_qty", "constraint_sql": "qty IS NOT NULL", "action": "fail"}]
    run = runner.run_pipeline(pdef)
    assert run.status == "failed" and "no_null_qty" in run.error
    assert "clean2" not in lt.list_tables()["silver"]  # never written


def test_idempotent_rerun(lake_home):
    from packs.dms.lakehouse.catalog import connect
    from packs.dms.pipelines import runner

    con = connect()
    _seed_raw(con)
    con.close()
    r1 = runner.run_pipeline(_DEF)
    r2 = runner.run_pipeline(_DEF)
    assert (r1.rows_in, r1.rows_out, r1.rows_dropped) == (r2.rows_in, r2.rows_out, r2.rows_dropped)


def test_event_log_records_runs(lake_home):
    from packs.dms.lakehouse.catalog import connect
    from packs.dms.pipelines import runner

    con = connect()
    _seed_raw(con)
    con.close()
    runner.run_pipeline(_DEF)
    events = runner.pipeline_events()
    assert events and events[0]["pipeline_id"] == "raw_to_silver"
    assert events[0]["rows_in"] == 4 and events[0]["rows_out"] == 2


def test_lineage_required_on_def(lake_home):
    from packs.dms.pipelines import runner

    bad = {k: v for k, v in _DEF.items() if k not in ("lineage", "lineage_reason")}
    with pytest.raises(runner.PipelineError, match="lineage"):
        runner.run_pipeline(bad)


def test_aggregate_requires_reason(lake_home):
    from packs.dms.pipelines import runner

    bad = dict(_DEF, lineage="aggregate", lineage_reason="")
    with pytest.raises(runner.PipelineError, match="lineage_reason"):
        runner.run_pipeline(bad)


def test_propagate_fails_without_provenance_cols(lake_home):
    from packs.dms.lakehouse.catalog import connect
    from packs.dms.pipelines import runner

    con = connect()
    _seed_raw(con)
    con.close()
    pdef = dict(
        _DEF,
        id="raw_propagate",
        target="silver.clean_prop",
        lineage="propagate",
        lineage_reason="",  # ignored for propagate
    )
    # remove reason key for propagate
    pdef.pop("lineage_reason", None)
    run = runner.run_pipeline(pdef)
    assert run.status == "failed"
    assert "propagate" in run.error


def test_propagate_ok_with_flat_src_cols(lake_home):
    from packs.dms.lakehouse.catalog import connect
    from packs.dms.pipelines import runner

    con = connect()
    _seed_raw(con)
    con.close()
    pdef = {
        "id": "raw_prop_ok",
        "source": "bronze.raw",
        "target": "silver.clean_prop_ok",
        "lineage": "propagate",
        "transform_sql": (
            "SELECT sku, TRY_CAST(TRIM(qty_txt) AS DOUBLE) AS qty, "
            "TRY_CAST(risk_txt AS DOUBLE) AS risk, "
            "1 AS _src_row, 'ref-1' AS _src_ref_id, 'ing-1' AS _ingest_id "
            "FROM {source}"
        ),
        "expectations": [
            {"name": "qty_non_negative", "constraint_sql": "qty >= 0", "action": "drop"},
        ],
    }
    run = runner.run_pipeline(pdef)
    assert run.status == "completed", run.error


def test_proposal_requires_approval(lake_home, monkeypatch):
    from packs.dms.lakehouse.catalog import connect
    from packs.dms.pipelines import propose

    # isolate proposed dir under tmp
    monkeypatch.setattr(propose, "PROPOSED_DIR", lake_home / "proposed")

    con = connect()
    _seed_raw(con)
    con.close()

    prop = propose.propose("bronze.raw", "silver.proposed_out")
    assert prop["status"] == "pending"
    # a pending proposal must NOT be runnable
    with pytest.raises(PermissionError):
        propose.run_if_approved(prop["id"])
    # approve, then it runs
    propose.approve_proposal(prop["id"], approver="steward")
    run = propose.run_if_approved(prop["id"])
    assert run.status == "completed"


def test_demo_def_against_migrated_bronze(lake_home):
    from scripts.lakehouse_migrate import migrate_all
    from packs.dms.lakehouse import tables as lt
    from packs.dms.pipelines import runner

    migrate_all()  # seeds bronze.suppliers_raw etc.
    run = runner.run_pipeline("suppliers_silver")
    assert run.status == "completed"
    assert run.rows_in == run.rows_out + run.rows_dropped
    assert run.rows_out > 0
    assert "supplier_id" in lt.read("silver", "suppliers", limit=1)[0]
