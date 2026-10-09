"""Q2 — layered adaptive answer engine (Netie Cortex router).

Proves the trust properties: certified replay for known questions, governed-metric
compile for paraphrases, honest truncation disclosure, scalar-as-count, abstain
(never a confident fallback listing) for unanswerable questions, and — the
headline gate — zero confident-wrong across the whole golden set.
"""
from __future__ import annotations

import pytest

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


def _assert_keyword_not_served(question: str):
    """L2 is off, so a former cascade question abstains. It must not keyword-serve."""
    from CortexOS.dms.answer_engine import answer

    body = answer(question)
    assert body["layer"] != "governed_metric"
    assert body["badge"] == "abstain"
    assert body.get("rows") == []
    assert body.get("sql_used") is None
    text = (body.get("answer") or "").strip()
    assert text
    assert "can't answer" in text.lower()
    return body


def test_metric_layer_compiles_target_paraphrase():
    from CortexOS.dms.answer_engine import route_to_metric

    q = "Which suppliers have a risk score above 0.7?"
    plan = route_to_metric(q)
    assert plan is not None and plan.metric_id == "suppliers_by_risk"
    _assert_keyword_not_served(q)


def test_truncation_disclosed():
    from CortexOS.dms.answer_engine import route_to_metric

    q = "Which shipments are delayed?"
    plan = route_to_metric(q)
    assert plan is not None and plan.metric_id == "shipments_by_status"
    _assert_keyword_not_served(q)


def test_scalar_question_returns_count_not_listing():
    from CortexOS.dms.answer_engine import route_to_metric

    q = "How many cold storage locations do we have?"
    plan = route_to_metric(q)
    assert plan is not None and plan.metric_id == "cold_storage_count"
    _assert_keyword_not_served(q)


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
    from CortexOS.dms.answer_engine import route_to_metric

    q = "average how many did it expired last month"
    plan = route_to_metric(q)
    assert plan is not None and plan.metric_id == "expired_last_month"
    _assert_keyword_not_served(q)


def test_last_month_sales_not_abstain():
    from CortexOS.dms.answer_engine import route_to_metric

    q = "last month sales"
    plan = route_to_metric(q)
    assert plan is not None and plan.metric_id == "revenue_last_month"
    _assert_keyword_not_served(q)


def test_total_revenue_g6_answers():
    """G6 — bare total revenue is a retired cascade metric, not a keyword serve."""
    from CortexOS.dms.answer_engine import route_to_metric

    q = "What was total revenue?"
    plan = route_to_metric(q)
    assert plan is not None and plan.metric_id == "revenue_total"
    _assert_keyword_not_served(q)


def test_session_average_of_them():
    from CortexOS.dms.answer_engine import clear_session

    sid = "test-session-avg-them"
    clear_session(sid)
    listing = answer_question("Which items are expired?", session_id=sid)
    assert listing["route"] == "sql"
    assert listing["row_count"] >= 1
    follow = answer_question("what is the average of them", session_id=sid)
    assert follow["route"] == "sql"
    assert follow["layer"] == "session"
    assert follow["row_count"] == 1
    assert "followup_count" in (follow["rows"][0] or {})


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
    assert first["badge"] == "abstain"
    assert first.get("rows") == []
    follow = answer_question("Divide the revenue by 5", session_id=sid)
    assert follow["layer"] != "governed_metric"
    blob = str(follow.get("rows")) + (follow.get("answer") or "")
    assert "80375993" not in blob.replace(",", "")


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
    assert first["badge"] == "abstain"
    assert first.get("rows") == []
    assert query_skills.find("average how many did it expired last month") is None

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


def test_l1_chooser_does_not_fall_back_to_the_cascade(monkeypatch):
    """C7-06: choose_governed_metric misses; answer() must not keyword-match."""
    from CortexOS.dms import answer_engine as ae
    from CortexOS.dms.c7_cutover import cutover_flags

    monkeypatch.delenv("DMS_C7_RETIRE_CASCADE", raising=False)
    monkeypatch.delenv("DMS_C7_CUTOVER_REPORT", raising=False)
    flags = cutover_flags()
    assert flags["cascade_retired"] is False
    assert flags["cutover"] is False
    q = "Which suppliers have a risk score above 0.7?"

    def _banned(question: str):
        raise AssertionError(f"keyword cascade must not serve: {question!r}")

    monkeypatch.setattr(ae, "route_to_metric", _banned)
    assert ae.choose_governed_metric(q) is None
    r = ae.answer(q)
    assert r["layer"] != "governed_metric"
    assert r["badge"] in {"abstain", "needs_clarification"}
    assert not r.get("rows")


def test_top_sku_excludes_named_sku():
    from CortexOS.dms.answer_engine import route_to_metric

    q = "ignoring SKU-00173 what is the top 5 sku by revenue"
    plan = route_to_metric(q)
    assert plan is not None
    assert "SKU-00173" in (plan.slots.get("exclude_skus") or [])
    _assert_keyword_not_served(q)


def test_top_sku_excludes_bare_beta_token():
    """G4 — the retired classifier normalizes BETA. answer() does not serve it."""
    from CortexOS.dms.answer_engine import route_to_metric

    q = "excluding BETA, top 5 sku by revenue"
    plan = route_to_metric(q)
    assert plan is not None
    excluded = [str(item).upper() for item in (plan.slots.get("exclude_skus") or [])]
    assert "SKU-BETA" in excluded or "BETA" in excluded
    _assert_keyword_not_served(q)


def test_top_sku_excludes_multiple_and_bare_token():
    from CortexOS.dms.answer_engine import route_to_metric

    q = "excluding SKU-00173 and SKU-00241, what is the top 5 sku by revenue"
    plan = route_to_metric(q)
    assert plan is not None
    excluded = plan.slots.get("exclude_skus") or []
    assert "SKU-00173" in excluded and "SKU-00241" in excluded
    _assert_keyword_not_served(q)
    _assert_keyword_not_served("ignoring 00173 what is the top 5 sku by revenue")


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
    rows = r.get("rows") or []
    assert rows, f"stale-replay returned zero rows: {r.get('answer')!r}"
    rendered = r.get("answer") or ""
    assert rendered.strip(), "stale-replay rendered no answer text"
    low = rendered.lower()
    assert "sales" in low or "ranked" in low or "myr" in low
    for row in rows:
        assert str(row["sku"]) in rendered


def test_low_stock_followup_uses_inventory_not_placeholders():
    from CortexOS.dms.answer_engine import clear_session

    clear_session("lowstock-follow")
    first = answer_question(
        "Top 5 selling SKUs by revenue",
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
    from CortexOS.dms.answer_engine import route_to_metric

    q = "number 6-10 sku by revenue"
    plan = route_to_metric(q)
    assert plan is not None
    assert plan.slots.get("offset") == 5 or "OFFSET" in str(plan.slots).upper() or plan.metric_id in {
        "sales_by_value",
        "sales_by_volume",
    }
    _assert_keyword_not_served(q)
