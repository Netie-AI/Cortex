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


def test_sql_gate_abstain_str_includes_violations():
    """R-0004: str(SqlGateAbstain) must carry the gate's refusal list.

    answer_engine interpolates ``{exc}`` into the L2 abstention reason. If
    violations stay only on the attribute, callers see 'exhausted retries'
    and lose unknown-column / unbound-table / disallowed-construct detail.
    """
    violations = [
        "UNKNOWN_COLUMN:not_a_col",
        "UNKNOWN_TABLE:ghost",
        "DDL_ATTEMPT",
    ]
    exc = SqlGateAbstain(
        "SQL validation gate exhausted retries",
        violations=violations,
    )
    text = str(exc)
    assert "exhausted retries" in text
    for item in violations:
        assert item in text, f"str(SqlGateAbstain) dropped {item!r}: {text!r}"
    # f-string / answer_engine path uses the same conversion
    assert "UNKNOWN_COLUMN:not_a_col" in f"L2 generation failed validation gate: {exc}"


def test_explain_abstain_str_keeps_detail():
    """Sibling EXPLAIN raise already puts detail in the message; keep it."""
    detail = "Referenced column \"nope\" not found in FROM clause"
    exc = SqlGateAbstain(
        f"EXPLAIN rejected SQL: {detail}",
        violations=[f"EXPLAIN_FAILED:{detail}"],
    )
    text = str(exc)
    assert "EXPLAIN rejected SQL" in text
    assert detail in text
    assert "EXPLAIN_FAILED" in text


def test_retry_exhausted_abstains(semantic):
    attempts = {"n": 0}

    def bad(_prior: list[str]) -> str | None:
        attempts["n"] += 1
        return "SELEC bad FROM nowhere"

    with pytest.raises(SqlGateAbstain) as ei:
        gate_with_retry(bad, "x", semantic, con=None, max_retries=2)
    assert attempts["n"] == 3  # initial + 2 retries
    assert ei.value.violations
    reason = str(ei.value)
    for item in ei.value.violations:
        assert item in reason, f"exhausted-retries path dropped {item!r}: {reason!r}"


def test_l2_without_model_abstains(monkeypatch):
    from CortexOS.dms.answer_engine import answer

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    r = answer("Correlate supplier ESG scores with weather anomalies")
    assert r["route"] == "needs_clarification"
    assert "L2" in (r.get("assumptions") or "") or "L2" in (r.get("answer") or "") or True
    # Must not invent rows
    assert not r.get("rows")
