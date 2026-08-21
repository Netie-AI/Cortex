"""ANS-02 - an aggregate over a ranking has no governed metric, so it abstains.

Every metric in the pack either ranks or aggregates; none does both. Asking for
"the sum of the top 5 selling SKUs" therefore has no plan, and the router used to
answer it anyway with whichever half it matched:

    sum of top 5 selling skus            -> the 5-row ranking (one number was asked for)
    average of the top 5 skus by revenue -> the 5-row ranking
    total revenue of the top 3 suppliers -> revenue_total, the WHOLE warehouse

The third is the one that matters. A five-row table when you asked for a total is
visibly not an answer; MYR 80,375,993.99 when you asked about three suppliers is a
plausible scalar under a governed badge, and nothing on screen says it is the
wrong question's answer. That is the ebd049b class.

Assertions here are on the RENDERED ANSWER TEXT and the RETURNED ROWS, never on
generated SQL - the acceptance clause says so explicitly, and a gate on an
intermediate artifact is how the original defect passed review (R-0001,
CLAUDE.md section 8).
"""

from __future__ import annotations

import pytest

from CortexOS.dms.answer_engine import _aggregate_over_ranking, answer, route_to_metric

# Aggregate governs the ranking -> no plan exists -> must abstain.
AGGREGATE_OVER_RANKING = [
    "i mean the sum of top 5 selling skus",
    "sum of top 5 selling skus",
    "total revenue of the top 3 suppliers",
    "average of the top 5 skus by revenue",
    "combined revenue of the top 10 skus",
    "what is the total of the highest selling products",
    "mean of the bottom 5 skus by revenue",
    "the summed value of the best performing categories",
]

# The aggregate is the ranking KEY, or there is no ranking at all. These are
# ordinary answerable questions and refusing them would be R-0005 in the other
# direction - a control that refuses legitimate work is a failure, not a win.
STILL_ANSWERABLE = [
    "top 5 selling skus by revenue",
    "top 3 categories by total revenue",
    "what is the total revenue",
    "total cold storage locations",
    "how many delayed shipments",
]


@pytest.mark.parametrize("question", AGGREGATE_OVER_RANKING)
def test_an_aggregate_over_a_ranking_abstains(question: str) -> None:
    """The customer receives an abstain, not a ranking and not a warehouse total."""
    result = answer(question, session_id="ans02-abstain")

    assert result["badge"] == "abstain", (
        f"{question!r} answered under badge {result['badge']!r}. A success badge on "
        f"the wrong question is worse than a refusal - the customer cannot tell."
    )
    assert result["rows"] == [], f"{question!r} returned rows: {result['rows']!r}"
    assert result["sql_used"] is None


@pytest.mark.parametrize("question", AGGREGATE_OVER_RANKING)
def test_the_abstain_says_why_and_names_the_path_that_works(question: str) -> None:
    """"Abstain AND SAY SO" - and saying so has to be actionable.

    The two-step form genuinely works: ask for the ranking, then "sum of them"
    returns the correct SUM scalar. An abstain that did not name it would be
    refusing work the system can actually do.
    """
    rendered = answer(question, session_id="ans02-why")["answer"]

    assert "ranking" in rendered.lower(), rendered
    assert "sum of them" in rendered.lower(), (
        f"the abstain for {question!r} does not name the two-step path that works. "
        f"Got: {rendered!r}"
    )


def test_the_warehouse_total_is_never_offered_as_the_answer() -> None:
    """The specific confidently-wrong scalar this ticket was filed about.

    "total revenue of the top 3 suppliers" returned revenue_total - every
    supplier in the warehouse - as though it were three. Pinned by value so a
    future reroute to the same metric fails here rather than in a demo.
    """
    result = answer("total revenue of the top 3 suppliers", session_id="ans02-scalar")

    assert result["rows"] == []
    assert "80375993" not in str(result["rows"]) + result["answer"].replace(",", "")


@pytest.mark.parametrize("question", STILL_ANSWERABLE)
def test_legitimate_questions_still_answer(question: str) -> None:
    """R-0005. Both words appear in "top 3 categories by total revenue" too."""
    result = answer(question, session_id="ans02-r0005")

    assert result["badge"] != "abstain", (
        f"{question!r} became an abstain. The aggregate here is the ranking KEY or "
        f"there is no ranking - refusing it is a regression, not a win."
    )
    assert result["rows"], f"{question!r} answered with no rows"


def test_the_two_step_path_still_computes_the_sum() -> None:
    """The path the abstain points at has to actually work.

    If this breaks, the abstain message becomes a lie and both routes refuse the
    same question - which is worse than the defect this ticket fixed.
    """
    answer("Top 5 selling SKUs by revenue", session_id="ans02-followup")
    result = answer("Sum of them", session_id="ans02-followup")

    assert result["rows"], "the follow-up sum returned nothing"
    value = next(iter(result["rows"][0].values()))
    assert float(value) > 0
    assert "sum" in result["answer"].lower()


def test_order_decides_it_not_membership() -> None:
    """The root-cause-class assertion (R-0004).

    Both an aggregate word and a ranking word appear in BOTH families. A
    membership test would refuse the legitimate half. What separates them is
    which one governs: an aggregate before the ranking applies to it, an
    aggregate after is the key the ranking sorts by.
    """
    assert _aggregate_over_ranking("sum of the top 5 skus") is not None
    assert _aggregate_over_ranking("top 5 skus by total revenue") is None

    # Same two words, order reversed, opposite verdict.
    assert _aggregate_over_ranking("total revenue of the top 3") is not None
    assert _aggregate_over_ranking("top 3 by total revenue") is None


def test_the_refusal_holds_for_callers_that_do_not_know_to_ask() -> None:
    """route_to_metric refuses on its own, not only via the answer path.

    The abstain *message* is rendered at the one answer site, but the refusal
    lives in route_to_metric so a future caller cannot reintroduce the defect by
    forgetting to check. Two places deciding one thing is how the exclusion
    defect (ANS-01) stayed alive through its first fix.
    """
    for question in AGGREGATE_OVER_RANKING:
        assert route_to_metric(question) is None, (
            f"route_to_metric still returns a plan for {question!r}"
        )
