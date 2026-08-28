"""Q1 vocabulary normalization — recall may widen, meaning may not move.

Normalization exists to let the L1 metric router recognise how people actually
ask ("what is running low in warehouse A"). Its danger is the mirror image: a
rewrite that quietly changes a number, a threshold, a direction or an entity
turns a safe abstention into a confident wrong answer. These tests pin the
boundary in both directions.
"""
from __future__ import annotations

import re

import pytest

from CortexOS.dms.answer_engine import route_to_metric
from packs.dms.semantic.vocabulary import (
    INVARIANT_TOKENS,
    normalize_for_routing,
)

# Business phrasing -> the metric it must now reach.
ROUTES = [
    ("what is running low in warehouse A", "low_stock"),
    ("which products need replenishment in warehouse A", "low_stock"),
    ("what needs reordering", "low_stock"),
    ("what stock has gone past its expiry date", "expired_items"),
    ("list SKUs past shelf life", "expired_items"),
    ("which freight companies are causing the delays", "count_by_carrier"),
    ("delay count per logistics provider", "count_by_carrier"),
    ("late inbound loads per site", "count_by_destination"),
    ("which sites are refrigerated", "cold_storage_list"),
    ("how many refrigerated sites do we run", "cold_storage_count"),
    ("where do we have the most room left", "free_capacity"),
    ("how full is each warehouse", "capacity_utilisation"),
    ("which sites are nearly full", "capacity_above"),
    ("what is currently flagged", "active_alerts"),
    ("suppliers with risk over 0.7", "suppliers_by_risk"),
    ("show me utilisation across locations", "capacity_utilisation"),
    ("what has not been topped up in a month", "stale_restock"),
    ("three highest selling SKUs by weight", "sales_by_volume"),
    ("which 3 SKUs moved the most kilograms", "sales_by_volume"),
]


@pytest.mark.parametrize("question,metric_id", ROUTES)
def test_business_phrasing_reaches_its_metric(question: str, metric_id: str) -> None:
    plan = route_to_metric(question)
    assert plan is not None, f"{question!r} abstained"
    assert plan.metric_id == metric_id, f"{question!r} -> {plan.metric_id}"


def test_volume_paraphrases_keep_the_asked_limit() -> None:
    for question in (
        "three highest selling SKUs by weight",
        "which 3 SKUs moved the most kilograms",
    ):
        plan = route_to_metric(question)
        assert plan is not None
        assert plan.metric_id == "sales_by_volume"
        assert plan.slots.get("limit") == 3, question


# ── invariants: normalization must not move meaning ──────────────────────────

MEANING_CRITICAL = [
    "which suppliers have a risk score above 0.7?",
    "which suppliers have a risk score below 0.7?",
    "which warehouse has the least free capacity",
    "which warehouse has the most free capacity",
    "top 3 SKUs by quantity sold",
    "which locations are above 90 percent capacity?",
    "which items were not restocked in the last 30 days?",
]


@pytest.mark.parametrize("question", MEANING_CRITICAL)
def test_invariant_tokens_survive_normalization(question: str) -> None:
    """No rule may add, drop or flip a comparison, direction or negation."""
    before = [t for t in re.findall(r"[a-z]+", question.lower()) if t in INVARIANT_TOKENS]
    after = [t for t in re.findall(r"[a-z]+", normalize_for_routing(question)) if t in INVARIANT_TOKENS]
    assert before == after, f"{question!r}: {before} -> {after}"


@pytest.mark.parametrize("question", MEANING_CRITICAL)
def test_numbers_survive_normalization(question: str) -> None:
    assert re.findall(r"\d+(?:\.\d+)?", question) == \
        re.findall(r"\d+(?:\.\d+)?", normalize_for_routing(question))


def test_slots_are_read_from_the_original_question() -> None:
    """The router reads slots from the raw text, so a rewrite of the *words*
    cannot move a *value*. Threshold, direction and limit are checked together."""
    plan = route_to_metric("which vendors have risk above 0.85")
    assert plan is not None and plan.metric_id == "suppliers_by_risk"
    assert plan.slots["threshold"] == 0.85

    least = route_to_metric("which site has the least spare space")
    assert least is not None and least.metric_id == "free_capacity"
    assert least.slots["direction"] == "ASC"

    most = route_to_metric("which site has the most spare space")
    assert most is not None and most.metric_id == "free_capacity"
    assert most.slots["direction"] == "DESC"


def test_normalization_is_idempotent() -> None:
    for question, _ in ROUTES:
        once = normalize_for_routing(question)
        assert normalize_for_routing(once) == once, question


def test_comparison_words_are_not_invented() -> None:
    """"worse than 0.7" MEANS "above 0.7" in this domain, but inferring that is a
    comparison rewrite — rule 2 forbids it, so the engine abstains instead of
    guessing which way the inequality points."""
    assert route_to_metric("which vendors score worse than 0.7 on risk") is None


def test_empty_input_is_safe() -> None:
    assert normalize_for_routing("") == ""
    assert normalize_for_routing("   ") == ""
