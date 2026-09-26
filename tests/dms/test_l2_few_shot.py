"""CX-FS-01 — dynamic few-shot for L2 Text2SQL from certified + steward-approved pairs.

Selection is asserted on the pairs returned; the prompt is captured through a
fake ``_freeroute_complete``; the end-to-end case asserts what the customer
receives (rendered answer text, rows, badge), not only the generated SQL.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from CortexOS.dms import l2_generation
from packs.dms.generative import few_shot, promotion, sql_generator
from packs.dms.generative.l2_adapter import DmsL2Generation

GOLD_Q = "Which locations are above 90 percent capacity?"
GOLD_SQL = "SELECT location_code FROM locations WHERE 100.0 * current_load_kg / capacity_kg > 90"
PARAPHRASE = "Which locations are over 90 percent of capacity?"


@pytest.fixture(autouse=True)
def _fresh_cache():
    few_shot.invalidate()
    yield
    few_shot.invalidate()


def _write_certified(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(yaml.safe_dump({"certified": rows}, sort_keys=False), encoding="utf-8")
    return path


def _promotion_db(tmp_path: Path) -> Path:
    """One approved, one pending (<5 uses), one rejected/unapproved surfaced pair."""
    db = tmp_path / "l2_promotion.sqlite"
    approved_q = "How many open purchase orders does each supplier carry?"
    approved_sql = "SELECT supplier_id, COUNT(*) AS n FROM shipments GROUP BY supplier_id"
    pending_q = "How many open purchase orders are pending per supplier?"
    pending_sql = "SELECT supplier_id, COUNT(*) AS pending_n FROM shipments GROUP BY 1"
    rejected_q = "How many open purchase orders per supplier were rejected?"
    rejected_sql = "SELECT supplier_id FROM shipments WHERE 1 = 0"
    for _ in range(promotion.PROMOTE_AFTER):
        promotion.record_validated(approved_q, approved_sql, path=db)
        promotion.record_validated(rejected_q, rejected_sql, path=db)
    promotion.record_validated(pending_q, pending_sql, path=db)
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "UPDATE l2_usage SET approved = 1 WHERE fingerprint = ?",
            (promotion.fingerprint(approved_q, approved_sql),),
        )
        con.commit()
    finally:
        con.close()
    return db


# -- selection ---------------------------------------------------------------------


def test_paraphrase_returns_closest_certified_pair() -> None:
    got = few_shot.select_examples(PARAPHRASE, k=3, tables=["locations", "inventory"])
    assert got, "paraphrase of a certified question found no example"
    assert got[0][0] == GOLD_Q
    assert " ".join(got[0][1].split()) == GOLD_SQL
    assert len(got) <= 3


def test_excludes_pairs_reading_tables_outside_reduced_schema() -> None:
    unrestricted = few_shot.select_examples(PARAPHRASE)
    assert any(q == GOLD_Q for q, _ in unrestricted)
    restricted = few_shot.select_examples(PARAPHRASE, tables=["inventory", "suppliers"])
    assert all(q != GOLD_Q for q, _ in restricted)
    for _, sql in restricted:
        assert set(few_shot.sql_tables(sql)) <= {"inventory", "suppliers"}


def test_multi_table_example_needs_every_table(tmp_path: Path) -> None:
    cert = _write_certified(
        tmp_path / "c.yaml",
        [
            {
                "id": "cq_join",
                "question": "Which SKUs are below reorder level in warehouse A?",
                "sql": "SELECT i.sku FROM inventory i JOIN locations l "
                "ON i.location_id = l.location_id WHERE l.location_code = 'WH-A'",
            }
        ],
    )
    q = "Which SKUs are under reorder level in warehouse A?"
    kw = {"certified_path": cert, "promotion_db": tmp_path / "none.sqlite"}
    assert few_shot.select_examples(q, tables=["inventory"], **kw) == []
    assert len(few_shot.select_examples(q, tables=["inventory", "locations"], **kw)) == 1


def test_only_steward_approved_promotions_are_examples(tmp_path: Path) -> None:
    cert = _write_certified(tmp_path / "c.yaml", [])
    db = _promotion_db(tmp_path)
    got = few_shot.select_examples(
        "How many open purchase orders per supplier?",
        k=5,
        tables=["shipments"],
        certified_path=cert,
        promotion_db=db,
    )
    sqls = [sql for _, sql in got]
    assert sqls == ["SELECT supplier_id, COUNT(*) AS n FROM shipments GROUP BY supplier_id"]
    loaded = few_shot.load_examples(certified_path=cert, promotion_db=db)
    assert [ex.source for ex in loaded] == ["promoted"]


def test_dedupe_by_normalised_sql_and_mtime_cache(tmp_path: Path) -> None:
    cert = _write_certified(
        tmp_path / "c.yaml",
        [
            {"id": "a", "question": "count skus", "sql": "SELECT COUNT(*) FROM inventory"},
            {"id": "b", "question": "sku total", "sql": "select   count(*) from inventory;"},
        ],
    )
    kw = {"certified_path": cert, "promotion_db": tmp_path / "none.sqlite"}
    assert len(few_shot.load_examples(**kw)) == 1
    import os

    _write_certified(
        cert,
        [
            {"id": "a", "question": "count skus", "sql": "SELECT COUNT(*) FROM inventory"},
            {"id": "c", "question": "list suppliers", "sql": "SELECT * FROM suppliers"},
        ],
    )
    st = cert.stat()
    os.utime(cert, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert len(few_shot.load_examples(**kw)) == 2


def test_unrelated_question_returns_nothing() -> None:
    assert few_shot.select_examples("what is the weather in paris tomorrow") == []
    assert few_shot.select_examples(
        "Who painted the Mona Lisa?", tables=["inventory", "locations", "suppliers"]
    ) == []


# -- prompt ------------------------------------------------------------------------


@pytest.fixture
def captured(monkeypatch) -> list[str]:
    prompts: list[str] = []

    def fake(prompt: str) -> str:
        prompts.append(prompt)
        return "SELECT location_code FROM locations LIMIT 5"

    monkeypatch.setattr(sql_generator, "is_configured", lambda: True)
    monkeypatch.setattr(sql_generator, "_freeroute_complete", fake)
    return prompts


_SCHEMA = {"tables": {"locations": {"columns": ["location_code"]}, "inventory": {}}}


def test_prompt_has_examples_block_before_question(monkeypatch, captured) -> None:
    monkeypatch.delenv("DMS_L2_FEW_SHOT", raising=False)
    port = DmsL2Generation()
    port.generate_candidates(PARAPHRASE, _SCHEMA)
    prompt = captured[-1]
    header = "EXAMPLES (verified question → SQL):"
    assert header in prompt
    assert prompt.index("REDUCED SCHEMA") < prompt.index(header) < prompt.index("QUESTION:")
    assert GOLD_SQL in prompt
    assert port.few_shot_count >= 1
    assert port.detail()["few_shot_count"] == port.few_shot_count


def test_examples_block_is_capped(monkeypatch, captured) -> None:
    long_pairs = [(f"question {i} " + "x" * 200, "SELECT 1 " + "y" * 300) for i in range(20)]
    monkeypatch.setattr(few_shot, "select_examples", lambda *a, **k: long_pairs)
    port = DmsL2Generation()
    port.generate_candidates(PARAPHRASE, _SCHEMA)
    prompt = captured[-1]
    start = prompt.index("EXAMPLES (")
    block = prompt[start : prompt.index("QUESTION:")].strip()
    assert len(block) <= sql_generator.EXAMPLES_CHAR_CAP
    assert 1 <= port.few_shot_count < len(long_pairs)
    assert block.count("\nQ: ") == port.few_shot_count


def test_few_shot_off_env_removes_block(monkeypatch, captured) -> None:
    monkeypatch.setenv("DMS_L2_FEW_SHOT", "0")
    port = DmsL2Generation()
    port.generate_candidates(PARAPHRASE, _SCHEMA)
    prompt = captured[-1]
    assert "EXAMPLES" not in prompt
    assert GOLD_SQL not in prompt
    assert "QUESTION:" in prompt
    assert port.few_shot_count == 0
    assert port.detail() == {"few_shot_count": 0, "few_shot_enabled": False}


def test_prior_violations_render_unchanged(monkeypatch, captured) -> None:
    port = DmsL2Generation()
    port.generate_candidates(PARAPHRASE, _SCHEMA, prior_violations=["bad column x"])
    prompt = captured[-1]
    assert prompt.endswith("PREVIOUS VALIDATION ERRORS (fix these):\n- bad column x")


# -- end to end: answer() on the paraphrase ----------------------------------------


def _gold_rows() -> list[dict[str, Any]]:
    from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection

    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        cur = con.execute(GOLD_SQL)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def _ask_paraphrase(monkeypatch) -> dict[str, Any]:
    from bench.accuracy import _ensure_db_loaded
    from CortexOS.dms.answer_engine import answer

    _ensure_db_loaded()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.delenv("DMS_L2_SHADOW", raising=False)
    port = DmsL2Generation()
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: port)
    monkeypatch.setattr(sql_generator, "is_configured", lambda: True)

    def fake(prompt: str) -> str | None:
        # A model that can only answer by copying a verified example.
        if "EXAMPLES (" not in prompt:
            return None
        head = prompt.split("QUESTION:")[0]
        for line in head.splitlines():
            if line.startswith("SQL: ") and "locations" in line:
                return line[len("SQL: ") :]
        return None

    monkeypatch.setattr(sql_generator, "_freeroute_complete", fake)
    for target in ("match_certified", "route_to_metric", "undefined_subject", "_shape_refusal"):
        monkeypatch.setattr(f"CortexOS.dms.answer_engine.{target}", lambda q: None)
    monkeypatch.setattr("packs.dms.semantic.query_skills.find", lambda *a, **k: None)
    monkeypatch.setattr("packs.dms.semantic.catalog_answer.is_catalog_intent", lambda q: False)
    env = answer(PARAPHRASE)
    env["_few_shot_count"] = port.few_shot_count
    return env


def test_answer_on_paraphrase_serves_gold_rows(monkeypatch) -> None:
    monkeypatch.delenv("DMS_L2_FEW_SHOT", raising=False)
    env = _ask_paraphrase(monkeypatch)
    gold = _gold_rows()
    assert gold, "gold SQL returns no rows; the fixture cannot prove anything"
    assert env["badge"] not in ("abstain", "ABSTAIN"), env.get("answer")
    assert env["badge"] == "L2_VALIDATED"
    assert env["rows"] == gold
    rendered = env.get("answer") or ""
    assert rendered.strip()
    for row in gold:
        assert str(row["location_code"]) in rendered
    assert env["_few_shot_count"] >= 1


def test_answer_abstains_without_examples(monkeypatch) -> None:
    monkeypatch.setenv("DMS_L2_FEW_SHOT", "0")
    env = _ask_paraphrase(monkeypatch)
    assert env["badge"] == "abstain"
    assert env["rows"] == []
    assert env["_few_shot_count"] == 0
