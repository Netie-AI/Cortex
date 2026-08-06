"""META-01 — catalog intent before L2; revenue stays governed."""
from __future__ import annotations

from CortexOS.dms.answer_engine import answer as engine_answer
from packs.dms.semantic.catalog_answer import is_catalog_intent


def test_meta01_founder_phrase_returns_catalog_not_abstain():
    assert is_catalog_intent("what else meta data that i can search for in the data")
    r = engine_answer("what else meta data that i can search for in the data")
    assert r["route"] != "needs_clarification"
    assert r["layer"] == "catalog"
    assert r["badge"] == "catalog"
    assert r["sql_used"] is None
    assert r["rows"] == []
    answer = (r.get("answer") or "").lower()
    assert "certified" in answer
    assert "table" in answer
    suggestions = r.get("suggestions") or []
    assert suggestions
    assert not all("cctv" in s.lower() for s in suggestions[:3])


def test_meta01_revenue_still_governed():
    from CortexOS.dms.answer_engine import route_to_metric

    q = "What was total revenue?"
    assert not is_catalog_intent(q)
    plan = route_to_metric(q)
    assert plan is not None
    assert plan.metric_id == "revenue_total"
