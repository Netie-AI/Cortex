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
def test_exact_sku_plus_conjunction_excludes_and_answers(
    question: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from packs.dms.semantic import query_skills

    monkeypatch.setattr(query_skills, "find", lambda _q: None)
    plan = route_to_metric(question)
    assert plan is not None
    assert plan.metric_id == "sales_by_value"
    body = answer_question(question)
    assert body["badge"] == "abstain"
    assert body["layer"] != "governed_metric"
    assert body.get("rows") == []
    assert body.get("sql_used") is None
    assert "can't answer" in (body.get("answer") or "").lower()


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


def test_dropping_rank_one_changes_the_rank_one_row(monkeypatch: pytest.MonkeyPatch) -> None:
    from packs.dms.semantic import query_skills

    monkeypatch.setattr(query_skills, "find", lambda _q: None)
    body = answer("show the top 5 SKUs by revenue")
    assert body["badge"] == "abstain"
    assert body.get("rows") == []
    plan = route_to_metric("ignore SKU-00173 and also show the top 5 SKUs by revenue")
    assert plan is not None
    assert "SKU-00173" in (plan.slots.get("exclude_skus") or [])


def test_two_named_skus_are_both_excluded() -> None:
    q = "exclude SKU-00173 and SKU-00241 and show top 5"
    plan = route_to_metric(q)
    assert plan is not None
    excluded = plan.slots.get("exclude_skus") or []
    assert "SKU-00173" in excluded and "SKU-00241" in excluded
    body = answer(q)
    assert body["badge"] == "abstain"
    assert body.get("rows") == []


def test_unknown_sku_shaped_token_abstains_rather_than_half_applying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packs.dms.semantic import query_skills

    monkeypatch.setattr(query_skills, "find", lambda _q: None)
    body = answer("exclude SKU-BETA and SKU-GAMMA and SKU-00397 and show top 5")
    assert body["badge"] == "abstain"
    assert body["rows"] == []
    assert body.get("sql_used") is None
    assert "SKU-GAMMA" not in (body.get("sql_used") or "")


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
