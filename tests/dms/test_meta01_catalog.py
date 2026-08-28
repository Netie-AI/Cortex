"""META-01 — catalog intent before L2; revenue stays governed."""
from __future__ import annotations

import pytest

from CortexOS.dms.answer_engine import answer as engine_answer
from packs.dms.semantic.catalog_answer import is_catalog_intent
from packs.dms.semantic.loader import load_all


@pytest.fixture(scope="module", autouse=True)
def ensure_db():
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.semantic.loader import reload

    _ensure_db_loaded()
    reload()
    yield


# Phrasings that used to split: two routed, two abstained. Also a couple of
# same-class wordings so the matcher is an intent, not a remembered list.
CATALOG_PHRASES = (
    "what can I ask",
    "what tables are available",
    "what data do you have",
    "show me the catalog",
    "what else meta data that i can search for in the data",
    "list available metrics",
    "what columns are there",
    "browse the ontology",
)


def _assert_catalog_payload(r: dict) -> None:
    assert r["route"] != "needs_clarification"
    assert r["layer"] == "catalog"
    # Catalog prose is never a green session grant.
    assert r["badge"] == "catalog"
    assert r["badge"] != "session"
    assert r["sql_used"] is None
    assert r["rows"] == []
    answer = r.get("answer") or ""
    assert answer.strip(), "catalog rendered no customer-visible text"
    low = answer.lower()
    assert "certified" in low
    assert "table" in low
    suggestions = r.get("suggestions") or []
    assert suggestions
    model = load_all()
    certified = {cq.question for cq in model.certified}
    metric_labels = {
        (m.synonyms[0] if m.synonyms else m.id.replace("_", " "))
        for m in model.metrics.values()
    }
    assert any(s in certified or s in metric_labels for s in suggestions)
    assert not all("cctv" in s.lower() for s in suggestions[:3])


@pytest.mark.parametrize("question", CATALOG_PHRASES)
def test_meta01_catalog_intent_class_routes(question: str):
    assert is_catalog_intent(question)
    _assert_catalog_payload(engine_answer(question))


def test_meta01_revenue_still_governed():
    from CortexOS.dms.answer_engine import route_to_metric

    q = "What was total revenue?"
    assert not is_catalog_intent(q)
    plan = route_to_metric(q)
    assert plan is not None
    assert plan.metric_id == "revenue_total"

    r = engine_answer(q)
    assert r["badge"] != "session"
    assert r["badge"] != "catalog"
    assert r["layer"] == "governed_metric"
    rows = r.get("rows") or []
    assert rows, f"revenue returned no rows: {r.get('answer')!r}"
    text = r.get("answer") or ""
    assert text.strip(), "revenue rendered no answer text"
    assert any(ch.isdigit() for ch in text)
    assert float(rows[0]["revenue_myr"]) > 0
