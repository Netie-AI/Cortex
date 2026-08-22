"""ANS-03 — a ranking grouped by a dimension must not return one warehouse row.

"top 3 categories by total revenue" used to compile ``revenue_total`` and
return [{"revenue_myr": 80375993.99}] under governed_metric. No metric ranks
categories by sales, so the honest outcome is to abstain and say why.
Assertions are on rendered text and returned rows, not SQL.
"""

from __future__ import annotations

import pytest

from CortexOS.dms.answer_engine import answer, route_to_metric
from CortexOS.dms.query_service import answer_question

GROUPED_RANKINGS = [
    "top 3 categories by total revenue",
    "top 5 categories by total revenue",
]


@pytest.fixture(scope="module", autouse=True)
def ensure_db():
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.semantic.loader import reload

    _ensure_db_loaded()
    reload()
    yield


def _warehouse_total_leaked(body: dict) -> bool:
    blob = str(body.get("rows")) + (body.get("answer") or "").replace(",", "")
    return "80375993" in blob


@pytest.mark.parametrize("question", GROUPED_RANKINGS)
def test_grouped_ranking_does_not_return_the_warehouse_as_one_row(
    question: str,
) -> None:
    body = answer(question)
    rows = body.get("rows") or []
    rendered = body.get("answer") or ""

    assert not _warehouse_total_leaked(body), (
        f"{question!r} returned the warehouse total under {body.get('badge')!r}: "
        f"{rows!r} / {rendered!r}"
    )
    if body.get("badge") == "abstain":
        assert rows == []
        assert body.get("sql_used") is None
        low = rendered.lower()
        assert "categor" in low
        assert "can't answer" in low or "no governed metric" in low
        assert route_to_metric(question) is None
        return

    assert len(rows) >= 2, (
        f"{question!r} answered with {len(rows)} row(s), not one row per group: "
        f"{rows!r}"
    )
    assert "category" in {str(k).lower() for k in rows[0]}
    asked = 3 if "top 3" in question else 5
    assert len(rows) <= asked


def test_sku_rank_that_names_total_revenue_still_ranks() -> None:
    """R-0005 — "top 5 SKUs by total revenue" ranks SKUs; it is not a scalar."""
    body = answer("top 5 SKUs by total revenue")
    assert body["badge"] != "abstain", body.get("answer")
    assert len(body.get("rows") or []) == 5
    assert "sku" in body["rows"][0]
    assert not _warehouse_total_leaked(body)
    rendered = body.get("answer") or ""
    assert "SKU-" in rendered


def test_bare_total_revenue_still_answers() -> None:
    body = answer_question("what is our total revenue")
    assert body["badge"] == "governed_metric"
    assert body["rows"]
    assert float(body["rows"][0]["revenue_myr"]) > 0
    assert any(ch.isdigit() for ch in (body.get("answer") or ""))


def test_unknown_subject_still_belongs_to_ans04() -> None:
    body = answer("top 3 customers by amount")
    assert body["badge"] == "abstain"
    assert body["rows"] == []
    assert "customer" in (body.get("answer") or "").lower()
