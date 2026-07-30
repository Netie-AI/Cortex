"""T7 drill-through rewrite + token tests (no DuckDB required for rewrite)."""

from __future__ import annotations

import time

import pytest

from CortexOS.execution.drillthrough import (
    DrillthroughTokenInvalid,
    mint_token,
    rewrite_for_drillthrough,
    sql_digest,
    verify_token,
)


def test_rewrite_strips_sum_keeps_where_and_join():
    sql = (
        "SELECT SUM(s.amount) AS total "
        "FROM silver.sales s "
        "JOIN silver.customer c ON c.id = s.customer_id "
        "WHERE s.quarter = 'Q3' AND c.region = 'Northern'"
    )
    out = rewrite_for_drillthrough(sql)
    low = out.sql.lower()
    assert "sum(" not in low
    assert "s.amount" in low or "amount" in low
    assert "silver.sales" in low
    assert "silver.customer" in low
    assert "quarter" in low and "northern" in low
    assert "limit 5000" in low
    assert "_src_ref_id" in low
    assert out.measure_expr is not None
    assert "count(" in out.count_sql.lower()


def test_rewrite_drops_group_by():
    sql = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region"
    out = rewrite_for_drillthrough(sql)
    assert "group by" not in out.sql.lower()


def test_token_roundtrip_and_expiry(monkeypatch):
    monkeypatch.setenv("CORTEX_DRILLTHROUGH_HMAC", "test-secret")
    tok = mint_token(
        answer_id="ans_1",
        session_id="sess_1",
        manifest_hash="abc",
        sql="SELECT 1",
        expires_at=time.time() + 60,
    )
    payload = verify_token(tok)
    assert payload["answer_id"] == "ans_1"
    assert payload["sql_digest"] == sql_digest("SELECT 1")

    expired = mint_token(
        answer_id="ans_1",
        session_id="sess_1",
        manifest_hash="abc",
        sql="SELECT 1",
        expires_at=time.time() - 1,
    )
    with pytest.raises(DrillthroughTokenInvalid, match="expired"):
        verify_token(expired)


def test_token_tamper_fails(monkeypatch):
    monkeypatch.setenv("CORTEX_DRILLTHROUGH_HMAC", "test-secret")
    tok = mint_token(
        answer_id="ans_1",
        session_id="sess_1",
        manifest_hash="abc",
        sql="SELECT 1",
    )
    bad = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    with pytest.raises(DrillthroughTokenInvalid):
        verify_token(bad)
