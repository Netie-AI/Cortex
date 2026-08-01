"""The follow-up turns a real session actually produced, pinned.

Every case here is taken from one reported conversation. The corpus was 376/376
with wrong=0 throughout that session, because it contained none of these shapes:
it tests questions, and this is the first suite that tests a *conversation*.

What went wrong, in one line each:

* "hmm sum of them?"                   -> followup_count = 491  (a count, for a sum)
* "i mean the sum of top 5 selling skus" -> sku_count = 509     (badged governed)
* "Sum of them"                        -> followup_count = 491, then 1
* "show me number 2-6"                 -> abstain
* "ignoring the first one wht would it be" -> abstain

The first three are the serious ones. A confident number next to the wrong
aggregation is worse than a refusal, because nothing on screen tells the
customer which question was answered.
"""

from __future__ import annotations

import pytest
from CortexOS.dms.answer_engine import ABSTAIN, clear_session
from CortexOS.dms.query_service import answer_question

TOP5 = "Top 5 selling SKUs by revenue"


@pytest.fixture
def session(request) -> str:
    sid = f"conv-{request.node.name}"
    clear_session(sid)
    return sid


def _skus(result: dict) -> list[str]:
    return [r.get("sku") for r in result.get("rows") or []]


# ---------------------------------------------------------------- aggregation


@pytest.mark.parametrize("phrasing", ["hmm sum of them?", "Sum of them", "sum them up"])
def test_sum_of_them_returns_the_sum_not_a_count(session: str, phrasing: str) -> None:
    top = answer_question(TOP5, session_id=session)
    expected = round(
        sum(float(r["sales_value_myr"]) for r in top["rows"] if r.get("sales_value_myr")), 2
    )

    follow = answer_question(phrasing, session_id=session)

    row = (follow.get("rows") or [{}])[0]
    assert "followup_count" not in row, f"{phrasing!r} answered a sum with a count"
    assert row.get("sum_sales_value_myr") == pytest.approx(expected, rel=1e-6)
    # What the customer reads, not just the row (CLAUDE.md section 8).
    assert "491" not in follow["answer"]


def test_count_of_them_still_counts(session: str) -> None:
    """The refusal must not have eaten the case that was working (R-0005)."""
    answer_question(TOP5, session_id=session)
    follow = answer_question("how many of them?", session_id=session)
    assert (follow["rows"] or [{}])[0].get("followup_count") == 5


def test_average_of_them_still_averages(session: str) -> None:
    answer_question(TOP5, session_id=session)
    follow = answer_question("what is the average of them", session_id=session)
    assert "avg_sales_value_myr" in (follow["rows"] or [{}])[0]


def test_sum_of_a_ranking_is_not_a_distinct_sku_count(session: str) -> None:
    """Standalone, not a follow-up: it must never answer 509.

    Returning the ranking is acceptable — the customer can see the five numbers.
    Returning the count of every SKU in the warehouse is not.
    """
    result = answer_question("i mean the sum of top 5 selling skus", session_id=session)
    row = (result.get("rows") or [{}])[0]
    assert row.get("sku_count") is None, "answered a sum with the count of all SKUs"
    assert "509" not in result["answer"]


# --------------------------------------------------------------------- window


def test_show_me_number_2_to_6_reslices_the_prior_ranking(session: str) -> None:
    top = answer_question(TOP5, session_id=session)
    first_five = _skus(top)

    follow = answer_question("show me number 2-6", session_id=session)

    assert follow["route"] != ABSTAIN
    assert follow["layer"] == "session"
    got = _skus(follow)
    assert got[:4] == first_five[1:5], "ranks 2-5 should carry over from the prior turn"
    assert len(got) == 5
    assert first_five[0] not in got, "rank 1 must be dropped"


def test_ignoring_the_first_one_drops_the_leader(session: str) -> None:
    top = answer_question(TOP5, session_id=session)
    leader = _skus(top)[0]

    follow = answer_question("ignoring the first one wht would it be", session_id=session)

    assert follow["route"] != ABSTAIN
    assert leader not in _skus(follow)


def test_a_question_naming_its_own_subject_is_not_re_windowed(session: str) -> None:
    """"top 2 to 6 SKUs by revenue" is a fresh ask, not a re-slice of what is on screen.

    The window follow-up only fires on a subjectless phrasing, so a complete
    question still goes through the governed metric and keeps its provenance.
    """
    answer_question(TOP5, session_id=session)
    fresh = answer_question("top 2 to 6 SKUs by revenue", session_id=session)
    assert fresh["layer"] == "governed_metric"


def test_window_follow_up_without_a_prior_turn_does_not_invent_one(session: str) -> None:
    """No previous ranking means there is nothing to re-slice — abstain, never guess."""
    result = answer_question("show me number 2-6", session_id=session)
    assert result["route"] == ABSTAIN
    assert result["rows"] == []
