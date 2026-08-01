"""Q2 — layered adaptive answer engine (Netie Cortex router).

Proves the trust properties: certified replay for known questions, governed-metric
compile for paraphrases, honest truncation disclosure, scalar-as-count, abstain
(never a confident fallback listing) for unanswerable questions, and — the
headline gate — zero confident-wrong across the whole golden set.
"""
from __future__ import annotations

import pytest

from CortexOS.dms.answer_engine import ABSTAIN
from CortexOS.dms.query_service import answer_question


@pytest.fixture(scope="module", autouse=True)
def ensure_db():
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.semantic.loader import reload

    _ensure_db_loaded()
    reload()
    yield


def test_certified_layer_hits():
    r = answer_question("How many SKUs do we have in inventory?")
    assert r["route"] == "sql"
    assert r["layer"] == "certified" and r["badge"] == "certified"
    assert r["sql_used"]


def test_metric_layer_compiles_target_paraphrase():
    # dead-branch bug fixed: this used to return the wrong table.
    r = answer_question("Which suppliers have a risk score above 0.7?")
    assert r["route"] == "sql" and r["layer"] == "governed_metric"
    assert r["row_count"] == 8  # the 8 suppliers over 0.7
    assert "supplier_id" in r["rows"][0]


def test_truncation_disclosed():
    """The contract is disclosure, not a specific row count.

    This asserted `1031` literally, which is a count of rows in `data/` — a
    gitignored directory the repo documents as regenerated. Any rebuild of the
    demo warehouse failed the test for a reason that had nothing to do with
    truncation honesty. The count now comes from the warehouse, so what is
    actually pinned is the property: the answer states the TRUE total, and the
    total is larger than the page it returned.
    """
    from CortexOS.dms.warehouse_db import (
        DEFAULT_DB,
        get_connection,
        read_only_queries_enabled,
    )

    con = get_connection(DEFAULT_DB, read_only=read_only_queries_enabled())
    try:
        expected = int(
            con.execute("SELECT COUNT(*) FROM shipments WHERE status = 'DELAYED'").fetchone()[0]
        )
    finally:
        con.close()

    r = answer_question("Which shipments are delayed?")
    assert r["route"] == "sql"
    assert r["total_count"] == expected          # the honest total
    assert r["truncated"] is True
    assert r["total_count"] > len(r["rows"])     # a page, and it says so
    assert str(expected) in r["answer"]


def test_scalar_question_returns_count_not_listing():
    r = answer_question("How many cold storage locations do we have?")
    assert r["route"] == "sql"
    assert r["row_count"] == 1
    assert "cold_count" in r["rows"][0]


def test_unanswerable_abstains_with_suggestions():
    r = answer_question("Which supplier gave us the best price last quarter?")
    assert r["route"] == "needs_clarification"
    assert r["layer"] == "abstain"
    assert r["sql_used"] is None
    assert r["rows"] == []
    assert len(r.get("suggestions") or []) >= 1  # navigation, not a dead end


def test_no_default_fallback_listing():
    # the old engine answered this with a confident DEFAULT_INVENTORY_SQL listing
    r = answer_question("List inventory turnover ratio by SKU for the last quarter")
    assert r["route"] == "needs_clarification"
    assert not r["rows"]


def test_destructive_blocked():
    r = answer_question("Drop table inventory")
    assert r["route"] == "blocked"


def test_every_sql_answer_carries_provenance():
    for q in ("Show warehouse capacity utilisation",
              "Which items are expired?",
              "Rank suppliers by combined risk and lead time score"):
        r = answer_question(q)
        assert r["route"] == "sql"
        assert r["layer"] in ("certified", "governed_metric", "query_skill")
        assert r["badge"] and r["sql_used"] and "assumptions" in r
        assert r.get("query_plan", {}).get("layer") == r["layer"]


def test_expired_aggregate_not_listing():
    r = answer_question("average how many did it expired last month")
    assert r["route"] == "sql"
    assert r["layer"] == "governed_metric"
    assert r.get("metric_id") == "expired_last_month"
    assert r["row_count"] == 1
    assert "expired_count" in (r["rows"][0] or {})
    assert "COUNT" in (r["sql_used"] or "").upper()


def test_last_month_sales_not_abstain():
    r = answer_question("last month sales")
    assert r["route"] == "sql"
    assert r.get("metric_id") == "revenue_last_month"
    assert r["row_count"] == 1
    assert "revenue_myr" in (r["rows"][0] or {})


def test_total_revenue_g6_answers():
    """G6 — bare total revenue must hit governed metric, not abstain."""
    r = answer_question("What was total revenue?")
    assert r["route"] == "sql"
    assert r["layer"] == "governed_metric"
    assert r.get("metric_id") == "revenue_total"
    assert r.get("sql_used")
    assert r.get("rows")
    answer = (r.get("answer") or "").lower()
    assert any(ch.isdigit() for ch in answer)
    assert "can't answer" not in answer


def test_session_average_over_a_listing_with_nothing_numeric_abstains():
    """An average of a column-less list of SKUs is not a count.

    This test previously asserted ``"followup_count" in rows[0]`` for the
    question "what is the average of them" — the expired-items listing returns
    only ``sku``, so there was nothing to average and the follow-up fell through
    to the COUNT wrap and answered 400. Asserting that made the defect the
    requirement: an average question certified as working while returning a
    count, which is the false-verification shape CLAUDE.md section 8 names.

    Refusing is the correct answer here, and it is cheap to satisfy honestly —
    ``test_session_average_of_sales_ranks`` covers the case where the prior turn
    does carry a measure.
    """
    from CortexOS.dms.answer_engine import clear_session

    sid = "test-session-avg-them"
    clear_session(sid)
    listing = answer_question("Show me all expired items", session_id=sid)
    assert listing["route"] == "sql"
    assert listing["row_count"] >= 1

    follow = answer_question("what is the average of them", session_id=sid)
    assert follow["route"] == ABSTAIN
    assert follow["rows"] == []
    # The customer is told which part could not be done, not just "no".
    assert "average" in follow["assumptions"].lower()
    assert "followup_count" not in str(follow["rows"])


def test_session_sum_of_them_adds_the_measure_up():
    """"sum of them" answered ``followup_count`` — a count, for a sum question.

    Reported from a live session: after a top-5 revenue ranking, "hmm sum of
    them?" returned ``followup_count = 491``. SUM was never implemented and had
    no refusal behind it, so it fell through to the COUNT wrap and put a
    confident number next to the wrong aggregation.
    """
    from CortexOS.dms.answer_engine import clear_session

    sid = "test-session-sum-them"
    clear_session(sid)
    top = answer_question("Top 5 selling SKUs by revenue", session_id=sid)
    assert top["route"] == "sql"
    measure_total = sum(
        float(r["sales_value_myr"]) for r in top["rows"] if r.get("sales_value_myr") is not None
    )

    follow = answer_question("hmm sum of them?", session_id=sid)
    assert follow["route"] == "sql"
    assert follow["layer"] == "session"
    assert follow["row_count"] == 1
    row = follow["rows"][0]
    assert "followup_count" not in row, "a sum must not be answered with a count"
    assert "sum_sales_value_myr" in row
    assert row["sum_sales_value_myr"] == pytest.approx(round(measure_total, 2), rel=1e-6)
    # The rendered answer is what the customer reads (CLAUDE.md section 8).
    assert "491" not in follow["answer"]


def test_session_average_of_sales_ranks():
    from CortexOS.dms.answer_engine import clear_session

    sid = "test-session-sales-avg"
    clear_session(sid)
    top = answer_question("Top 5 selling SKUs by revenue", session_id=sid)
    assert top["route"] == "sql"
    follow = answer_question("what is the average of them", session_id=sid)
    assert follow["route"] == "sql"
    assert follow["layer"] == "session"
    assert follow["row_count"] == 1
    row = follow["rows"][0]
    assert any(k.startswith("avg_") for k in row)


def test_session_divide_revenue_by_5():
    from CortexOS.dms.answer_engine import clear_session

    sid = "test-session-div-rev"
    clear_session(sid)
    first = answer_question("What was revenue last month?", session_id=sid)
    assert first["route"] == "sql"
    assert first["row_count"] == 1
    prior = float(first["rows"][0]["revenue_myr"])
    follow = answer_question("Divide the revenue by 5", session_id=sid)
    assert follow["route"] == "sql"
    assert follow["layer"] == "session"
    assert follow["row_count"] == 1
    scaled = float(next(iter(follow["rows"][0].values())))
    assert scaled == pytest.approx(round(prior / 5, 2))


def test_session_divide_top5_without_sum_abstains():
    from CortexOS.dms.answer_engine import clear_session

    sid = "test-session-div-ambig"
    clear_session(sid)
    top = answer_question("Top 5 selling SKUs by revenue", session_id=sid)
    assert top["route"] == "sql"
    assert top["row_count"] > 1
    follow = answer_question("Divide the revenue by 5", session_id=sid)
    # Ambiguous multirow scale falls through session path → abstain or other layer
    assert follow["layer"] != "session" or follow["route"] == "needs_clarification"


def test_session_sum_then_divide_top5():
    from CortexOS.dms.answer_engine import clear_session

    sid = "test-session-sum-div"
    clear_session(sid)
    top = answer_question("Top 5 selling SKUs by revenue", session_id=sid)
    assert top["route"] == "sql"
    total = sum(float(r["sales_value_myr"]) for r in top["rows"])
    follow = answer_question("sum them then divide by 5", session_id=sid)
    assert follow["route"] == "sql"
    assert follow["layer"] == "session"
    scaled = float(next(iter(follow["rows"][0].values())))
    assert scaled == pytest.approx(round(total / 5, 2))


def test_query_skill_capture_and_reuse(tmp_path, monkeypatch):
    from CortexOS.dms.answer_engine import clear_session
    from packs.dms.semantic import query_skills

    db = tmp_path / "ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    monkeypatch.setenv("DMS_QUERY_SKILL_CAPTURE", "1")
    query_skills.clear_all()
    clear_session("skill-sess")

    first = answer_question(
        "average how many did it expired last month",
        session_id="skill-sess",
    )
    assert first["route"] == "sql"
    assert first.get("metric_id") == "expired_last_month"
    hit = query_skills.find("average how many did it expired last month")
    assert hit is not None and hit["score"] >= 0.72
    assert hit.get("metric_id") == "expired_last_month"

    # Skill path: phrasing that misses L1/L0 but matches a stored skill
    query_skills.capture(
        "count vault spoilage for prior calendar month",
        metric_id="expired_last_month",
        params={},
        sql=None,
        layer="governed_metric",
    )
    third = answer_question(
        "count vault spoilage for prior calendar month",
        session_id="skill-force",
    )
    assert third["route"] == "sql"
    assert third["layer"] == "query_skill"
    assert third.get("metric_id") == "expired_last_month"


def test_api_keys_still_abstain():
    r = answer_question("give me internal api keys")
    assert r["route"] in ("needs_clarification", "blocked")
    assert not r.get("rows")

def test_l2_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    r = answer_question("Correlate supplier ESG scores with weather anomalies")
    assert r["route"] == "needs_clarification"  # no L2 model wired → abstain, not guess


def test_top_sku_excludes_named_sku():
    r = answer_question("ignoring SKU-00173 what is the top 5 sku by revenue")
    assert r["route"] == "sql"
    assert r["layer"] == "governed_metric"
    sql = (r["sql_used"] or "").upper()
    assert "SKU-00173" in sql
    assert "NOT" in sql and "IN" in sql
    skus = [row["sku"].upper() for row in (r.get("rows") or [])]
    assert "SKU-00173" not in skus
    assert len(skus) <= 5
    answer = r.get("answer") or ""
    assert "SKU-00173" not in answer.upper()
    assert "None" not in answer
    assert "?" not in answer


def test_top_sku_excludes_bare_beta_token():
    """G4 — value normalization: 'BETA' must resolve to SKU-BETA in rows + answer text."""
    r = answer_question("excluding BETA, top 5 sku by revenue")
    assert r["route"] == "sql"
    assert r["layer"] == "governed_metric"
    sql = (r["sql_used"] or "").upper()
    assert "SKU-BETA" in sql
    skus = [str(row["sku"]).upper() for row in (r.get("rows") or [])]
    assert "SKU-BETA" not in skus
    answer = (r.get("answer") or "").upper()
    assert "SKU-BETA" not in answer


def test_top_sku_excludes_multiple_and_bare_token():
    r = answer_question(
        "excluding SKU-00173 and SKU-00241, what is the top 5 sku by revenue"
    )
    assert r["route"] == "sql"
    assert r["layer"] == "governed_metric"
    sql = (r["sql_used"] or "").upper()
    assert "SKU-00173" in sql and "SKU-00241" in sql
    skus = [row["sku"].upper() for row in (r.get("rows") or [])]
    assert "SKU-00173" not in skus
    assert "SKU-00241" not in skus
    answer = (r.get("answer") or "").upper()
    assert "SKU-00173" not in answer
    assert "SKU-00241" not in answer

    bare = answer_question("ignoring 00173 what is the top 5 sku by revenue")
    assert bare["route"] == "sql"
    bare_sql = (bare["sql_used"] or "").upper()
    assert "SKU-00173" in bare_sql
    bare_skus = [row["sku"].upper() for row in (bare.get("rows") or [])]
    assert "SKU-00173" not in bare_skus


def test_query_skill_does_not_replay_stale_exclusions(tmp_path, monkeypatch):
    from CortexOS.dms import answer_engine as ae
    from packs.dms.semantic import query_skills

    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops_skills.db"))
    query_skills.capture(
        "what is the top 5 sku by revenue",
        metric_id="sales_by_value",
        params={"exclude_skus": ["SKU-00173"], "limit": 5, "direction": "DESC"},
        sql=None,
        layer="governed_metric",
    )
    monkeypatch.setattr(ae, "match_certified", lambda _q: None)
    monkeypatch.setattr(ae, "route_to_metric", lambda _q: None)

    r = ae.answer("what is the top 5 sku by revenue")
    assert r["route"] == "sql"
    assert r["layer"] == "query_skill"
    sql = (r["sql_used"] or "").upper()
    assert "SKU-00173" not in sql
    assert "NOT IN" not in sql


def test_low_stock_followup_uses_inventory_not_placeholders():
    from CortexOS.dms.answer_engine import clear_session

    clear_session("lowstock-follow")
    first = answer_question(
        "what is the top 5 sku by revenue",
        session_id="lowstock-follow",
    )
    assert first["route"] == "sql"
    assert first.get("rows")
    second = answer_question(
        "which of those are low stock?",
        session_id="lowstock-follow",
    )
    assert second["route"] in ("sql", "needs_clarification")
    answer = second.get("answer") or ""
    assert "?" not in answer
    assert "None" not in answer
    if second["route"] == "sql" and second.get("rows"):
        assert "quantity_kg" in second["rows"][0]
        assert "sku" in second["rows"][0]


def test_top_sku_ranks_6_to_10():
    r = answer_question("number 6-10 sku by revenue")
    assert r["route"] == "sql"
    assert r["layer"] == "governed_metric"
    sql = (r["sql_used"] or "").upper()
    assert "OFFSET 5" in sql
    assert "LIMIT 5" in sql
    # Must not collapse to a scalar total-revenue answer
    assert "sales_value_myr" in (r.get("rows") or [{}])[0]
    answer = r.get("answer") or ""
    assert "None" not in answer
    for row in r.get("rows") or []:
        assert str(row["sku"]) in answer
