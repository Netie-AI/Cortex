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
    "mean of the bottom 5 skus by revenue",
]

# Rankings that never say how many. `_RANKING` reaches these through its
# participle branch rather than its count branch, which is the half that
# distinguishes "the highest SELLING skus" from "bottom LINE revenue": a ranking
# continues into a participle, a compound noun continues into a noun. Kept as
# their own family so that if the participle branch is ever removed, the failure
# names the reason instead of looking like a stray phrasing.
COUNTLESS_RANKINGS = [
    "what is the total of the highest selling products",
    "the summed value of the best performing categories",
    "average value of the highest selling skus",
]

# The aggregate is the ranking KEY, or there is no ranking at all. These are
# ordinary answerable questions and refusing them would be R-0005 in the other
# direction - a control that refuses legitimate work is a failure, not a win.
#
# "top 3 categories by total revenue" is deliberately NOT here. It answers, so
# it looks like it belongs - and it returns the whole warehouse's revenue as one
# row (Cortex#38). Listing it would have pinned a confidently wrong answer as
# the behaviour that must not change, which is what the first version of this
# file did.
STILL_ANSWERABLE = [
    "top 5 selling skus by revenue",
    "what is the total revenue",
    "total cold storage locations",
    "how many delayed shipments",
]

# Questions the first version of this guard refused. Each had always worked, and
# each broke for a different reason: "i mean" contains the aggregate word MEAN,
# and `\btop\b` matches inside "top-level" and "bottom line" because a hyphen
# and a space are both word boundaries. Kept as a family so the next widening of
# either word list has to run past them.
NEVER_REFUSE = [
    "i mean top 5 skus",
    "i mean the top 3 suppliers by revenue",
    "total bottom line revenue",
    "total revenue by top-level category",
]

# Aggregate-over-ranking written with words the first version did not list.
# Three of these returned the exact defect figure - 80,375,993.99 - by swapping
# one synonym for "top", which is why "the listed phrasings are fixed" is not
# the same claim as "the class is closed".
ESCAPES_FROM_THE_FIRST_VERSION = [
    "total revenue of the leading 3 suppliers",
    "total revenue of the foremost 3 suppliers",
    "total revenue of the poorest 3 suppliers",
    "cumulative revenue of the top 3 suppliers",
    "overall revenue of the top 3 suppliers",
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
    """R-0005 - and asserted on what came back, not on the fact something did.

    The first version of this test asserted `badge != "abstain"` and `rows`
    truthy. "top 3 categories by total revenue" satisfies both while returning
    the whole warehouse's revenue as a single row, so the control certified a
    confidently wrong answer as the behaviour that must not change (Cortex#38).
    Asserting the row COUNT the question implies is what separates "answered"
    from "answered correctly" (R-0001).
    """
    result = answer(question, session_id="ans02-r0005")

    assert result["badge"] != "abstain", (
        f"{question!r} became an abstain. The aggregate here is the ranking KEY or "
        f"there is no ranking - refusing it is a regression, not a win."
    )
    assert result["rows"], f"{question!r} answered with no rows"

    if question.startswith("top 5"):
        assert len(result["rows"]) == 5, (
            f"{question!r} asked for 5 and returned {len(result['rows'])} row(s): "
            f"{result['rows']!r}. A ranking answered with one aggregate row is the "
            f"wrong question answered confidently, not a pass."
        )
    else:
        assert len(result["rows"]) == 1, result["rows"]


@pytest.mark.parametrize("question", NEVER_REFUSE)
def test_the_guard_does_not_refuse_what_it_never_should_have(question: str) -> None:
    """The regressions the first version of this guard introduced.

    "i mean top 5 skus" is the one that matters: "i mean" is the commonest
    repair phrase in this feature - the ticket's own canonical question opens
    with it - and reading it as an average refused a question that had always
    worked. It also renamed the reason, so the abstain for "i mean the SUM of
    top 5" announced a MEAN.
    """
    result = answer(question, session_id="ans02-never-refuse")

    assert result["badge"] != "abstain", (
        f"{question!r} was refused. It contains an aggregate word and a ranking "
        f"word, and it is still an ordinary question - "
        f"got: {result['answer']!r}"
    )
    assert result["rows"], f"{question!r} answered with no rows"


def test_the_discourse_leadin_does_not_rename_the_reason() -> None:
    """"i mean the sum of ..." must blame the sum, not the "mean" in "i mean"."""
    rendered = answer(
        "i mean the sum of top 5 selling skus", session_id="ans02-leadin"
    )["answer"].lower()

    assert "computes a sum over a ranking" in rendered, rendered
    assert "mean over a ranking" not in rendered, rendered


@pytest.mark.parametrize("question", ESCAPES_FROM_THE_FIRST_VERSION)
def test_synonyms_do_not_walk_past_the_guard(question: str) -> None:
    """One synonym away from "top" reached the defect figure unchanged.

    These are still word lists, so this family is a floor and not proof the
    class is closed - see the docstring on `_aggregate_over_ranking`. Pinned so
    a narrowing of either list has to fail here first.
    """
    result = answer(question, session_id="ans02-escapes")

    assert result["badge"] == "abstain", (
        f"{question!r} answered under badge {result['badge']!r} with "
        f"{result['rows']!r}"
    )
    assert "80375993" not in str(result["rows"]).replace(",", "")


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


@pytest.mark.parametrize("question", COUNTLESS_RANKINGS)
def test_a_ranking_that_names_no_count_is_still_a_ranking(question: str) -> None:
    """The participle branch, and why the count branch alone was not enough.

    Requiring a count is what stopped "bottom line revenue" and "top-level
    category" being read as rankings, but it also let every countless ranking
    through - "average value of the highest selling skus" went back to returning
    the five-row ranking when an average was asked for.

    The participle branch recovers them without letting the noun phrases back
    in. These must abstain for the ANS-02 reason, not merely fail to match a
    metric, so the reason is asserted here too.
    """
    result = answer(question, session_id="ans02-countless")

    assert result["badge"] == "abstain", (
        f"{question!r} answered under {result['badge']!r} with {result['rows']!r}"
    )
    assert result["rows"] == []
    assert "over a ranking" in result["answer"].lower(), result["answer"]
