"""ANS-02 — an aggregate over a ranking has no governed metric, so it abstains.

The router used to answer "sum of top 5 selling skus" with the five-row
ranking, badged governed_metric. Assertions are on rendered text and rows.
"""

from __future__ import annotations

import pytest

from CortexOS.dms.answer_engine import _aggregate_over_ranking, answer, route_to_metric

AGGREGATE_OVER_RANKING = [
    "i mean the sum of top 5 selling skus",
    "sum of top 5 selling skus",
    "total revenue of the top 3 suppliers",
    "average of the top 5 skus by revenue",
    "combined revenue of the top 10 skus",
]

STILL_ANSWERABLE = [
    "top 5 selling skus by revenue",
    "what is the total revenue",
    "total cold storage locations",
    "how many delayed shipments",
    "i mean top 5 skus",
]

ESCAPES = [
    "total revenue of the leading 3 suppliers",
    "cumulative revenue of the top 3 suppliers",
    "overall revenue of the top 3 suppliers",
]


@pytest.fixture(scope="module", autouse=True)
def ensure_db():
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.semantic.loader import reload

    _ensure_db_loaded()
    reload()
    yield


@pytest.mark.parametrize("question", AGGREGATE_OVER_RANKING)
def test_an_aggregate_over_a_ranking_abstains(question: str) -> None:
    body = answer(question)
    assert body["badge"] == "abstain", (
        f"{question!r} answered under {body['badge']!r} with {body.get('rows')!r}"
    )
    assert body["rows"] == []
    assert body.get("sql_used") is None
    rendered = (body.get("answer") or "").lower()
    assert "ranking" in rendered
    assert "sum of them" in rendered
    assert route_to_metric(question) is None


def test_the_warehouse_total_is_never_offered_as_the_answer() -> None:
    body = answer("total revenue of the top 3 suppliers")
    blob = str(body.get("rows")) + (body.get("answer") or "").replace(",", "")
    assert body["rows"] == []
    assert "80375993" not in blob


@pytest.mark.parametrize("question", STILL_ANSWERABLE)
def test_legitimate_questions_still_answer(question: str) -> None:
    body = answer(question)
    assert body["badge"] != "abstain", (
        f"{question!r} became an abstain: {body.get('answer')!r}"
    )
    assert body["rows"]
    rendered = body.get("answer") or ""
    assert rendered.strip()
    if "top 5" in question:
        assert len(body["rows"]) == 5, body["rows"]
        assert "sku" in body["rows"][0]


def test_discourse_leadin_does_not_rename_the_reason() -> None:
    rendered = answer("i mean the sum of top 5 selling skus")["answer"].lower()
    assert "computes a sum over a ranking" in rendered
    assert "mean over a ranking" not in rendered


@pytest.mark.parametrize("question", ESCAPES)
def test_synonyms_do_not_walk_past_the_guard(question: str) -> None:
    body = answer(question)
    assert body["badge"] == "abstain"
    assert "80375993" not in str(body.get("rows")).replace(",", "")


def test_the_two_step_path_still_computes_the_sum() -> None:
    answer("Top 5 selling SKUs by revenue", session_id="ans02-followup")
    body = answer("Sum of them", session_id="ans02-followup")
    assert body["rows"]
    value = next(iter(body["rows"][0].values()))
    assert float(value) > 0
    assert "sum" in (body.get("answer") or "").lower()


def test_order_decides_it_not_membership() -> None:
    assert _aggregate_over_ranking("sum of the top 5 skus") is not None
    assert _aggregate_over_ranking("top 5 skus by total revenue") is None
    assert _aggregate_over_ranking("total revenue of the top 3") is not None
    assert _aggregate_over_ranking("top 3 by total revenue") is None
