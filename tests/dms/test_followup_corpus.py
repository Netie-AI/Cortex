"""FOLLOWUP-02 — compute-or-abstain corpus for grounded follow-ups."""

from __future__ import annotations

import statistics

import pytest

from CortexOS.dms.answer_engine import ABSTAIN, clear_session
from CortexOS.dms.query_service import answer_question

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


@pytest.mark.parametrize(
    ("setup", "follow_up", "expect_key"),
    [
        (TOP5, "hmm sum of them?", "sum_sales_value_myr"),
        (TOP5, "what is the average of them", "avg_sales_value_myr"),
        (TOP5, "what is the median of them", "median_sales_value_myr"),
        (TOP5, "how many of them?", "followup_count"),
        (TOP5, "give me their mean", "avg_sales_value_myr"),
        (TOP5, "their total", "sum_sales_value_myr"),
    ],
)
def test_followup_corpus_computes_or_abstains_honestly(
    session: str, setup: str, follow_up: str, expect_key: str
) -> None:
    top = answer_question(setup, session_id=session)
    _assert_sql_envelope(top)
    assert top["rows"]

    follow = answer_question(follow_up, session_id=session)
    _assert_sql_envelope(follow)

    assert follow["route"] != ABSTAIN, f"{follow_up!r} abstained unexpectedly"
    row = (follow.get("rows") or [{}])[0]
    assert expect_key in row, f"{follow_up!r} missing {expect_key!r}"
    if expect_key.startswith(("sum_", "avg_", "median_")):
        vals = [
            float(r["sales_value_myr"])
            for r in top["rows"]
            if r.get("sales_value_myr") is not None
        ]
        if expect_key.startswith("sum_"):
            expected = round(sum(vals), 2)
        elif expect_key.startswith("avg_"):
            expected = round(sum(vals) / len(vals), 2)
        else:
            expected = round(statistics.median(vals), 2)
        assert row[expect_key] == pytest.approx(expected, rel=1e-6)
        assert str(int(expected)) in follow["answer"] or str(expected) in follow["answer"]


@pytest.mark.parametrize(
    "follow_up",
    [
        "what is the average of them",
        "sum of them",
        "give me their mean",
    ],
)
def test_followup_without_prior_abstains(session: str, follow_up: str) -> None:
    result = answer_question(follow_up, session_id=session)
    _assert_sql_envelope(result)
    assert result["route"] == ABSTAIN
    assert result["badge"] == "abstain"
