"""Tests for DMS Brain profiler, cleaner, guardrail, and query service."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def messy_csv(tmp_path_factory):
    from netie.dms.generate_sample import generate_rows
    import csv

    path = tmp_path_factory.mktemp("dms") / "warehouse_messy.csv"
    rows = generate_rows(200)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def test_profiler_flags_messy_issues(messy_csv):
    from netie.dms.profiler import profile_dataset

    report = profile_dataset(messy_csv)
    types = {i.issue_type for i in report.detected_issues}
    assert "UNIT_INCONSISTENCY" in types
    assert "CATEGORICAL_VARIANTS" in types
    assert "FORMAT_INCONSISTENCY" in types
    assert "DUPLICATES" in types
    assert "TYPE_CHAOS" in types


def test_cleaner_produces_consistent_csv(messy_csv, tmp_path):
    from netie.dms.cleaner import clean_dataset
    from netie.dms.profiler import profile_dataset

    clean = tmp_path / "clean.csv"
    changelog = tmp_path / "changelog.jsonl"
    out = clean_dataset(messy_csv, output_path=clean, changelog_path=changelog)
    assert out.is_file()
    assert changelog.stat().st_size > 0
    report = profile_dataset(out)
    unit_issues = [i for i in report.detected_issues if i.issue_type == "UNIT_INCONSISTENCY"]
    assert unit_issues == []
    assert report.row_count < 220


def test_sql_guardrail_blocks_ddl():
    from netie.dms.sql_guardrail import validate_sql
    from netie.dms.warehouse_db import load_semantic_layer

    sem = load_semantic_layer()
    r = validate_sql("DROP TABLE inventory", sem)
    assert not r.passed
    assert "DDL_ATTEMPT" in r.violations

    r2 = validate_sql("DELETE FROM inventory WHERE sku='X'", sem)
    assert not r2.passed

    r3 = validate_sql("SELECT * FROM passwords", sem)
    assert not r3.passed
    assert any("UNKNOWN_TABLE" in v for v in r3.violations)


def test_sql_guardrail_passes_select_with_limit():
    from netie.dms.sql_guardrail import validate_sql
    from netie.dms.warehouse_db import load_semantic_layer

    sem = load_semantic_layer()
    r = validate_sql(
        "SELECT sku, quantity_kg, reorder_level_kg FROM inventory WHERE quantity_kg < reorder_level_kg",
        sem,
    )
    assert r.passed
    assert r.safe_sql and "LIMIT" in r.safe_sql.upper()


@pytest.fixture(scope="module")
def loaded_db(messy_csv, tmp_path_factory):
    from netie.dms.cleaner import clean_dataset
    from netie.dms.warehouse_db import load_inventory_csv

    d = tmp_path_factory.mktemp("db")
    clean = d / "clean.csv"
    db = d / "demo.duckdb"
    clean_dataset(messy_csv, output_path=clean, changelog_path=d / "log.jsonl")
    load_inventory_csv(clean, db)
    return db


def test_query_low_stock(loaded_db, monkeypatch):
    from netie.dms import query_service
    from netie.dms.warehouse_db import get_connection

    monkeypatch.setattr(query_service, "DEFAULT_DB", loaded_db)
    monkeypatch.setattr(
        "netie.dms.query_service.get_connection",
        lambda _=None, **kw: get_connection(loaded_db, **kw),
    )
    result = query_service.answer_question(
        "Which SKUs are below reorder level in warehouse A?"
    )
    assert result["violations_blocked"] == []
    assert result["sql_used"]
    rows = result.get("rows") or []
    assert rows, f"low-stock listing empty: {result.get('answer')!r}"
    for row in rows:
        assert row.get("sku")
        qty = float(row["quantity_kg"])
        assert qty >= 0
        reorder = row.get("reorder_level_kg", row.get("reorder_level"))
        if reorder is not None:
            assert qty < float(reorder)
    rendered = result.get("answer") or ""
    assert rendered.strip(), "low-stock rendered no answer text"
    low = rendered.lower()
    assert "below reorder" in low or "reorder level" in low
    for row in rows[:8]:
        assert str(row["sku"]) in rendered


def test_query_top_sales_respects_top_n(loaded_db, monkeypatch):
    from netie.dms import query_service
    from netie.dms.warehouse_db import get_connection

    monkeypatch.setattr(query_service, "DEFAULT_DB", loaded_db)
    monkeypatch.setattr(
        "netie.dms.query_service.get_connection",
        lambda _=None, **kw: get_connection(loaded_db, **kw),
    )
    result = query_service.answer_question("Top 5 selling SKUs by revenue")
    assert result["violations_blocked"] == []
    assert result["source_table"] == "transactions"
    assert result["row_count"] == 5
    assert "LIMIT 5" in result["sql_used"].upper()
    # Certified L0 still serves this exact question. The keyword phrase
    # "top 5 sales" is not a serve path.
    assert result["query_plan"]["intent"] in {
        "sales_by_value",
        "certified",
        "cq_sales_top5_value",
    }


def test_query_most_delayed_can_request_guardrail_cap(loaded_db, monkeypatch):
    from netie.dms import query_service
    from netie.dms.warehouse_db import get_connection

    monkeypatch.setattr(query_service, "DEFAULT_DB", loaded_db)
    monkeypatch.setattr(
        "netie.dms.query_service.get_connection",
        lambda _=None, **kw: get_connection(loaded_db, **kw),
    )
    result = query_service.answer_question("return most delayed 1000 rows")
    # The delayed/late word bridge is retired. A miss abstains. It must not
    # serve the legacy ranked listing.
    assert result["route"] == "needs_clarification"
    assert result["sql_used"] is None
    assert result.get("rows") in ([], None)
    assert result["badge"] in {"abstain", "needs_clarification"}


def test_query_random_question_asks_for_dms_clarification():
    from netie.dms import query_service

    result = query_service.answer_question("how do I lean back in my chair?")
    assert result["route"] == "needs_clarification"
    assert result["sql_used"] is None
    assert "DMS semantic layer" in result["answer"]


def test_query_blocks_drop(loaded_db, monkeypatch):
    from netie.dms import query_service

    monkeypatch.setattr(query_service, "DEFAULT_DB", loaded_db)
    result = query_service.answer_question("Drop the inventory table")
    assert "not permitted" in result["answer"].lower()
    assert result["violations_blocked"]
