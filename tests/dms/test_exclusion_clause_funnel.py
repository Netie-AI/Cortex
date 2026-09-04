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
    body = answer_question(question)
    rows = body.get("rows") or []
    assert rows, (
        f"{question!r} abstained instead of applying the exclusion - "
        f"badge={body.get('badge')!r} answer={body.get('answer')!r}"
    )
    assert body["badge"] != "abstain"
    assert body["route"] == "sql"
    rendered = str(body.get("answer") or "")
    assert rendered.strip(), "rendered answer was empty"
    assert "SKU-BETA" not in rendered.upper()
    assert all("BETA" not in str(row.get("sku", "")).upper() for row in rows)
    assert "?" not in rendered


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
    baseline = answer("show the top 5 SKUs by revenue")
    leaders = [row.get("sku") for row in (baseline.get("rows") or [])]
    assert leaders, "baseline ranking is empty"
    leader = leaders[0]

    body = answer(f"ignore {leader} and also show the top 5 SKUs by revenue")
    rows = body.get("rows") or []
    assert rows, f"excluding {leader} abstained: {body.get('answer')!r}"
    got = [row.get("sku") for row in rows]
    assert leader not in got
    assert leader not in str(body.get("answer") or "")
    assert body["badge"] != "abstain"


def test_two_named_skus_are_both_excluded() -> None:
    baseline = answer("show the top 5 SKUs by revenue")
    leaders = [row.get("sku") for row in (baseline.get("rows") or [])]
    assert len(leaders) >= 2
    first, second = leaders[0], leaders[1]

    body = answer(f"exclude {first} and {second} and show top 5")
    rows = body.get("rows") or []
    assert rows, f"two-SKU exclusion abstained: {body.get('answer')!r}"
    got = [row.get("sku") for row in rows]
    assert first not in got and second not in got
    rendered = str(body.get("answer") or "")
    assert first not in rendered and second not in rendered


def test_unknown_sku_shaped_token_abstains_rather_than_half_applying() -> None:
    body = answer("exclude SKU-BETA and SKU-GAMMA and SKU-00397 and show top 5")
    assert body["badge"] == "abstain"
    assert body["rows"] == []
    assert "SKU-GAMMA" in str(body.get("answer") or "")


def test_route_still_compiles_sales_rank() -> None:
    plan = route_to_metric("ignore SKU-BETA and show the top 5 SKUs by revenue")
    assert plan is not None
    assert plan.metric_id == "sales_by_value"
    assert plan.slots.get("exclude_skus") == ["SKU-BETA"]


def test_exclusion_and_also_show_is_not_l1_composition() -> None:
    from CortexOS.dms.answer_engine import _l1_cannot_compose

    assert _l1_cannot_compose(
        "ignore SKU-BETA and also show the top 5 SKUs by revenue"
    ) is None
    assert _l1_cannot_compose(
        "Count DELAYED shipments whose SKU is marked hazardous and whose "
        "destination location is a cold-storage site."
    ) is not None
