"""ANS-01 — an exact SKU plus a conjunction must exclude and answer.

Two lists used to disagree about ``and`` / ``dan``. The clause now ends at
the entities that resolve, so a trailing conjunction or adverb cannot force
a confirm chip. Assertions are on rendered text and returned rows, not SQL.
"""

from __future__ import annotations

import pytest

from CortexOS.dms.answer_engine import _excluded_skus, answer, route_to_metric
from CortexOS.dms.query_service import answer_question

TICKET_PHRASINGS = [
    "ignore SKU-BETA and show the top 5 SKUs by revenue",
    "ignore BETA and show the top 5 SKUs by revenue",
    "keluarkan BETA dari top 5 sku revenue",
]

ADVERB_PHRASINGS = [
    "ignore SKU-BETA and also show the top 5 SKUs by revenue",
    "exclude SKU-BETA and just show the top 5 SKUs by revenue",
    "buang BETA dan tunjukkan top 5 sku revenue",
]


@pytest.fixture(scope="module", autouse=True)
def ensure_db():
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.semantic.loader import reload

    _ensure_db_loaded()
    reload()
    yield


@pytest.mark.parametrize("question", TICKET_PHRASINGS + ADVERB_PHRASINGS)
def test_exact_sku_plus_conjunction_excludes_and_answers(question: str) -> None:
    """#412: these phrasings are not the declared ranking question."""
    body = answer_question(question)
    assert body["badge"] == "reconfirm", body.get("answer")
    assert body.get("rows") in ([], None)
    assert body.get("sql_used") is None
    rendered = str(body.get("answer") or "")
    assert "Yes runs that question" in rendered
    assert "not found in the database" in rendered


def test_exclusion_slot_strips_trailing_skip_tokens() -> None:
    assert _excluded_skus("ignore SKU-BETA and show the top 5 SKUs by revenue") == [
        "SKU-BETA"
    ]
    assert _excluded_skus("ignore SKU-BETA and also show the top 5 SKUs by revenue") == [
        "SKU-BETA"
    ]
    assert _excluded_skus("keluarkan BETA dari top 5 sku revenue") == ["BETA"]
    assert "ALSO" not in _excluded_skus(
        "ignore SKU-00397 and also show the top 5 SKUs by revenue"
    )


def test_dropping_rank_one_changes_the_rank_one_row() -> None:
    baseline = answer("Top 5 selling SKUs by revenue")
    leaders = [row.get("sku") for row in (baseline.get("rows") or [])]
    assert leaders, "baseline ranking is empty"
    leader = leaders[0]

    body = answer(f"ignore {leader} and also show the top 5 SKUs by revenue")
    assert body["badge"] == "reconfirm", body.get("answer")
    assert body.get("rows") in ([], None)
    assert leader not in str(body.get("answer") or "")


def test_two_named_skus_are_both_excluded() -> None:
    baseline = answer("Top 5 selling SKUs by revenue")
    leaders = [row.get("sku") for row in (baseline.get("rows") or [])]
    assert len(leaders) >= 2
    first, second = leaders[0], leaders[1]

    body = answer(f"exclude {first} and {second} and show top 5")
    assert body["badge"] == "reconfirm", body.get("answer")
    assert body.get("rows") in ([], None)
    rendered = str(body.get("answer") or "")
    assert first not in rendered and second not in rendered


def test_unknown_sku_shaped_token_abstains_rather_than_half_applying() -> None:
    body = answer("exclude SKU-BETA and SKU-GAMMA and SKU-00397 and show top 5")
    assert body["badge"] == "reconfirm"
    assert body["rows"] == []
    assert body.get("sql_used") is None


def test_route_still_compiles_sales_rank() -> None:
    from CortexOS.dms.answer_engine import _route_to_metric_cascade

    question = "ignore SKU-BETA and show the top 5 SKUs by revenue"
    neighbour = _route_to_metric_cascade(question)
    assert neighbour is not None
    assert neighbour.metric_id == "sales_by_value"
    assert neighbour.slots.get("exclude_skus") == ["SKU-BETA"]
    assert route_to_metric(question) is None


def test_exclusion_and_also_show_is_not_l1_composition() -> None:
    from CortexOS.dms.answer_engine import _l1_cannot_compose

    assert _l1_cannot_compose(
        "ignore SKU-BETA and also show the top 5 SKUs by revenue"
    ) is None
    assert _l1_cannot_compose(
        "Count DELAYED shipments whose SKU is marked hazardous and whose "
        "destination location is a cold-storage site."
    ) is not None
