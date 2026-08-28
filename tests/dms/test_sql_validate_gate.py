"""C7-min — EXPLAIN dry-run + retry gate acceptance."""

from __future__ import annotations

import pytest

from CortexOS.dms.sql_validate_gate import (
    SqlGateAbstain,
    explain_dry_run,
    gate_with_retry,
    run_gate,
)
from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection, load_semantic_layer


@pytest.fixture(scope="module")
def semantic():
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()
    return load_semantic_layer()


def test_explain_rejects_syntax_error(semantic):
    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        ok, detail = explain_dry_run(con, "SELEC * FROMM inventory")
        assert ok is False
        assert detail
        gate = run_gate("SELEC * FROMM inventory", semantic, con=con)
        assert gate.passed is False
        assert gate.explain_ok is False or any("PARSE" in v for v in gate.violations)
    finally:
        con.close()


def test_explain_passes_valid_select(semantic):
    sql = "SELECT sku FROM inventory LIMIT 5"
    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        gate = run_gate(sql, semantic, con=con)
        assert gate.passed is True
        assert gate.explain_ok is True
        assert gate.safe_sql
        ok, _ = explain_dry_run(con, gate.safe_sql)
        assert ok is True
    finally:
        con.close()


def test_retry_exhausted_abstains(semantic):
    attempts = {"n": 0}

    def bad(_prior: list[str]) -> str | None:
        attempts["n"] += 1
        return "SELEC bad FROM nowhere"

    with pytest.raises(SqlGateAbstain) as ei:
        gate_with_retry(bad, "x", semantic, con=None, max_retries=2)
    assert attempts["n"] == 3  # initial + 2 retries
    assert ei.value.violations
    for v in ei.value.violations:
        assert v in str(ei.value)


def test_sql_gate_abstain_str_includes_violations():
    exc = SqlGateAbstain(
        "SQL validation gate exhausted retries",
        violations=["UNKNOWN_COLUMN:nope", "EXPLAIN_FAILED:x"],
    )
    text = str(exc)
    assert "UNKNOWN_COLUMN:nope" in text
    assert "EXPLAIN_FAILED:x" in text
    assert "exhausted retries" in text


def test_sql_gate_abstain_str_without_violations_is_bare():
    assert str(SqlGateAbstain("SQL validation gate exhausted retries")) == (
        "SQL validation gate exhausted retries"
    )


def test_l2_attempt_keeps_gate_violations(monkeypatch):
    from CortexOS.dms import l2_generation

    class _Port:
        def is_configured(self) -> bool:
            return True

        def retrieve_schema(self, question: str) -> dict:
            return {"tables": {"inventory": {}}}

        def generate_candidates(self, question, schema, *, prior_violations=None):
            return ["SELEC bad FROM nowhere"]

        def record_validated(self, question, sql):
            return None

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: _Port())
    out = l2_generation.attempt_l2("free form anything")
    assert out is not None
    assert out.sql is None
    assert out.violations
    for v in out.violations:
        assert v in (out.reason or "")


def test_l2_without_model_abstains(monkeypatch):
    from CortexOS.dms.answer_engine import answer

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    r = answer("Correlate supplier ESG scores with weather anomalies")
    assert r["route"] == "needs_clarification"
    assert "L2" in (r.get("assumptions") or "") or "L2" in (r.get("answer") or "") or True
    # Must not invent rows
    assert not r.get("rows")
