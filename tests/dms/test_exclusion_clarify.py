"""Exclusion clarify + sku_name fuzzy resolve tests."""

from __future__ import annotations

from CortexOS.dms.answer_engine import (
    _clarify_exclusion,
    _resolve_exclusions,
    _sales_rank_slots,
    answer,
)


def test_resolve_beta_exact_encoding():
    exact, clarify = _resolve_exclusions("exclude beta from the top 5 sales")
    assert exact == ["SKU-BETA"]
    assert clarify is None


def test_resolve_beta_trial_phrase_fuzzy():
    exact, clarify = _resolve_exclusions("remove beta trial from the top 5 sales")
    assert exact == []
    assert clarify is not None
    assert clarify["kind"] == "exclusion_confirm"
    assert clarify["sku"] == "SKU-BETA"


def test_sales_rank_slots_clarify_not_silent_exclude():
    slots = _sales_rank_slots("remove beta trial from the top 5 sales")
    assert "exclude_skus" not in slots
    assert slots.get("_exclusion_clarify", {}).get("sku") == "SKU-BETA"


def test_answer_exclusion_clarify_suggestions():
    out = answer("remove beta trial from the top 5 sales", session_id="test-excl")
    assert out["route"] == "needs_clarification"
    assert out["badge"] == "abstain"
    assert any(s.startswith("Yes — exclude SKU-BETA") for s in out["suggestions"])
    assert any("without excluding" in s for s in out["suggestions"])
    assert out.get("sql_used") is None


def test_clarify_helper_shapes_yes_no():
    out = _clarify_exclusion(
        "q",
        "aid",
        clarify={"kind": "exclusion_confirm", "sku": "SKU-BETA", "phrase": "beta trial"},
        limit=5,
    )
    assert "SKU-BETA" in out["answer"]
    assert out["suggestions"][0].startswith("Yes —")
