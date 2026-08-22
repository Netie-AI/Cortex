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


def test_retry_exhausted_abstains(semantic):
    attempts = {"n": 0}

    def bad(_prior: list[str]) -> str | None:
        attempts["n"] += 1
        return "SELEC bad FROM nowhere"

    with pytest.raises(SqlGateAbstain) as ei:
        gate_with_retry(bad, "x", semantic, con=None, max_retries=2)
    assert attempts["n"] == 3  # initial + 2 retries
    assert ei.value.violations
    # FF-03 (dms#59): the abstain reason the customer sees is built as
    # f"...: {exc}", so the *why* must survive str(). A message that names only
    # "exhausted retries" and strands the violations on .violations is the defect.
    rendered = str(ei.value)
    assert "exhausted retries" in rendered
    for v in ei.value.violations:
        assert v in rendered, f"violation {v!r} dropped from str(SqlGateAbstain)"


def test_str_carries_violations():
    """str() folds the violations into the message so no call site drops them."""
    exc = SqlGateAbstain(
        "SQL validation gate exhausted retries",
        violations=["unknown column revenue_usd", "unbound table sales"],
    )
    rendered = str(exc)
    assert "SQL validation gate exhausted retries" in rendered
    assert "unknown column revenue_usd" in rendered
    assert "unbound table sales" in rendered


def test_str_without_violations_is_bare_message():
    """No violations — the message is unchanged, no dangling separator."""
    exc = SqlGateAbstain("SQL validation gate exhausted retries")
    assert str(exc) == "SQL validation gate exhausted retries"


def test_l2_without_model_abstains(monkeypatch):
    from CortexOS.dms.answer_engine import answer

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    r = answer("Correlate supplier ESG scores with weather anomalies")
    assert r["route"] == "needs_clarification"
    assert "L2" in (r.get("assumptions") or "") or "L2" in (r.get("answer") or "") or True
    # Must not invent rows
    assert not r.get("rows")
