"""Phase 1a seed gates and Phase 1b expansion gates.

The Phase 1b tests below guard the property that makes the expansion worth
anything: paraphrases must be the *same question* as their parent. If a rewrite
moves a number or drops an identifier, it is scoring against gold that no longer
describes it, and a green corpus stops meaning anything.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SEEDS_PATH = ROOT / "bench" / "corpus" / "seeds_v1.yaml"
PARAPHRASES_PATH = ROOT / "bench" / "corpus" / "paraphrases_v1.yaml"
THRESHOLDS_PATH = ROOT / "bench" / "thresholds.yaml"

PHASE1_CATEGORIES = (
    "grain_fanout",
    "null_semantics",
    "silent_dedup",
    "temporal",
    "rare_sql",
    "unit_currency",
    "semantic_ambiguity",
    "malay_codeswitch",
    "must_abstain",
    "coercion",
    "value_normalization",
    "fallback_hazard",
)


def test_twelve_categories_declared():
    from bench.corpus import PHASE1_CATEGORIES

    assert len(PHASE1_CATEGORIES) == 12


def test_seed_floors_met():
    thresholds = yaml.safe_load(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    seeds = yaml.safe_load(SEEDS_PATH.read_text(encoding="utf-8"))
    floors = thresholds.get("category_seed_floors") or {}
    for cat in PHASE1_CATEGORIES:
        entries = (seeds.get("categories") or {}).get(cat) or []
        min_n = int(floors.get(cat, 3))
        assert len(entries) >= min_n, f"{cat}: {len(entries)} seeds < floor {min_n}"


def test_every_seed_has_engineer_intent():
    seeds = yaml.safe_load(SEEDS_PATH.read_text(encoding="utf-8"))
    for cat, entries in (seeds.get("categories") or {}).items():
        for e in entries or []:
            assert e.get("engineer_intent"), f"{cat}/{e['id']} missing engineer_intent"


@pytest.fixture(scope="module")
def corpus_report():
    from bench.corpus import run_offline

    return run_offline()


def test_corpus_confidently_wrong_zero(corpus_report):
    assert corpus_report["totals"]["wrong"] == 0


def test_gated_categories_wrong_zero(corpus_report):
    thresholds = yaml.safe_load(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    by_cat = corpus_report["by_category"]
    for cat in thresholds.get("gated_categories") or []:
        summary = by_cat.get(cat) or {"wrong": 0}
        assert summary.get("wrong", 0) == 0, f"{cat}: wrong={summary.get('wrong')}"


# ── Phase 1b — paraphrase expansion ──────────────────────────────────────────

#: Tokens a paraphrase is never allowed to change: they select rows or set a
#: limit. Invariant 11 — numbers and identifiers come from user text.
_ENTITY = re.compile(r"\b(?:SKU-[A-Z0-9-]+|[A-Z]{2,}-[A-Z0-9-]+|\d{2,})\b")


@pytest.fixture(scope="module")
def paraphrases():
    from bench.corpus import load_paraphrases, load_seeds

    seeds = load_seeds()
    return {s.id: s for s in seeds}, load_paraphrases(seeds)


def test_every_seed_has_paraphrases(paraphrases):
    by_id, expanded = paraphrases
    thresholds = yaml.safe_load(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    floor = int((thresholds.get("corpus") or {}).get("paraphrases_min_per_seed", 5))
    counts: dict[str, int] = {}
    for item in expanded:
        counts[item.parent_id] = counts.get(item.parent_id, 0) + 1
    for seed_id in by_id:
        assert counts.get(seed_id, 0) >= floor, f"{seed_id}: {counts.get(seed_id, 0)} < {floor}"


def test_corpus_reaches_the_claim_target_in_size(paraphrases):
    """Size is necessary but not sufficient — see the gold_verified test below."""
    by_id, expanded = paraphrases
    thresholds = yaml.safe_load(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    target = int((thresholds.get("corpus") or {}).get("target_total", 310))
    assert len(by_id) + len(expanded) >= target


def test_paraphrases_preserve_entities_and_numbers(paraphrases):
    """A rewrite that drops SKU-00173 is scoring against the wrong gold."""
    by_id, expanded = paraphrases
    for item in expanded:
        parent = by_id[item.parent_id]
        for token in set(_ENTITY.findall(parent.question)):
            assert token.lower() in item.question.lower(), (
                f"{item.id}: parent names {token} but the paraphrase does not — "
                "entities may never be rewritten"
            )


def test_paraphrases_inherit_gold_never_author_it(paraphrases):
    by_id, expanded = paraphrases
    for item in expanded:
        parent = by_id[item.parent_id]
        assert item.canonical_sql == parent.canonical_sql
        assert item.match == parent.match
        assert item.category == parent.category


def test_expanded_items_are_unverified_until_a_human_says_otherwise(paraphrases):
    """The gate itself. Machine-authored questions must not inflate the claim."""
    _, expanded = paraphrases
    raw = yaml.safe_load(PARAPHRASES_PATH.read_text(encoding="utf-8"))
    assert raw.get("default_gold_verified") is False
    for item in expanded:
        if item.gold_verified:
            # Only an explicit mapping entry may be verified, and it must say who.
            group = raw["paraphrases"][item.parent_id]
            idx = int(item.id.rsplit("#p", 1)[1]) - 1
            entry = group[idx]
            assert isinstance(entry, dict) and entry.get("verified_by"), (
                f"{item.id} is marked verified without a reviewer name"
            )


def test_claim_n_counts_only_verified_items(corpus_report):
    sizes = corpus_report["corpus"]
    assert sizes["claim_n"] == sizes["expanded_n"] - sizes["unverified_n"]
    assert sizes["claim_n"] <= sizes["expanded_n"]


def test_wrong_is_zero_across_the_whole_expansion(corpus_report):
    """Unverified items are still scored — a confident wrong is a defect anywhere."""
    assert corpus_report["corpus"]["expanded_totals"]["wrong"] == 0
    assert corpus_report["corpus"]["claim_totals"]["wrong"] == 0
