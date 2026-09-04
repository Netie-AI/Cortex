"""FOLLOWUP-02 — compute-or-abstain corpus for grounded follow-ups."""

from __future__ import annotations

import statistics

import pytest

from CortexOS.dms.answer_engine import ABSTAIN, answer, clear_session

TOP5 = "Top 5 selling SKUs by revenue"


def _assert_sql_envelope(result: dict) -> None:
    """Customer-visible fields the DMS envelope must carry (Phase 0)."""
    assert "badge" in result
    assert "answer" in result
    assert "rows" in result
    assert "route" in result
    if result["route"] == ABSTAIN:
        assert result["badge"] == "abstain"
        assert result["rows"] == []
    else:
        assert result["badge"] in (
            "certified",
            "governed_metric",
            "session",
            "query_skill",
            "L2_VALIDATED",
        )


@pytest.fixture
def session(request) -> str:
    sid = f"corp-{request.node.name}"
    clear_session(sid)
    return sid


def _expected_from_top(top_rows: list[dict], expect_key: str):
    vals = [
        float(r["sales_value_myr"])
        for r in top_rows
        if r.get("sales_value_myr") is not None
    ]
    if expect_key.startswith("sum_"):
        return round(sum(vals), 2)
    if expect_key.startswith("avg_"):
        return round(sum(vals) / len(vals), 2)
    if expect_key.startswith("median_"):
        return round(statistics.median(vals), 2)
    if expect_key.startswith("min_"):
        return round(min(vals), 2)
    if expect_key.startswith("max_"):
        return round(max(vals), 2)
    if expect_key == "followup_count":
        return len(top_rows)
    raise AssertionError(f"unknown expect_key {expect_key!r}")


@pytest.mark.parametrize(
    ("setup", "follow_up", "expect_key"),
    [
        (TOP5, "hmm sum of them?", "sum_sales_value_myr"),
        (TOP5, "what is the average of them", "avg_sales_value_myr"),
        (TOP5, "how many of them?", "followup_count"),
        (TOP5, "sum of those", "sum_sales_value_myr"),
        (TOP5, "average of these", "avg_sales_value_myr"),
        (TOP5, "count of these", "followup_count"),
    ],
)
def test_followup_corpus_computes_or_abstains_honestly(
    session: str, setup: str, follow_up: str, expect_key: str
) -> None:
    top = answer(setup, session_id=session)
    _assert_sql_envelope(top)
    assert top["rows"]

    follow = answer(follow_up, session_id=session)
    _assert_sql_envelope(follow)

    assert follow["route"] != ABSTAIN, f"{follow_up!r} abstained unexpectedly"
    row = (follow.get("rows") or [{}])[0]
    assert expect_key in row, f"{follow_up!r} missing {expect_key!r}"
    # Named agg must never silently become a different aggregation (wrong=0).
    if expect_key != "followup_count":
        assert "followup_count" not in row, f"{follow_up!r} answered with a count"
    expected = _expected_from_top(top["rows"], expect_key)
    assert row[expect_key] == pytest.approx(expected, rel=1e-6)
    if expect_key != "followup_count":
        assert str(int(expected)) in follow["answer"] or str(expected) in follow["answer"]


@pytest.mark.parametrize(
    "follow_up",
    [
        "what is the average of them",
        "sum of them",
        "how many of them?",
    ],
)
def test_followup_without_prior_abstains(session: str, follow_up: str) -> None:
    result = answer(follow_up, session_id=session)
    _assert_sql_envelope(result)
    assert result["route"] == ABSTAIN
    assert result["badge"] == "abstain"


def test_sum_phrasing_never_returns_a_count(session: str) -> None:
    """Adversarial: sum-shaped follow-up must not invent a count (wrong=0)."""
    answer(TOP5, session_id=session)
    follow = answer("hmm sum of them?", session_id=session)
    _assert_sql_envelope(follow)
    row = (follow.get("rows") or [{}])[0]
    assert "followup_count" not in row
    assert "sum_sales_value_myr" in row
