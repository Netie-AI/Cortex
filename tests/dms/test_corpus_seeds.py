"""Phase 1a seed gates and Phase 1b expansion gates.

The Phase 1b tests below guard the property that makes the expansion worth
anything: paraphrases must be the *same question* as their parent. If a rewrite
moves a number or drops an identifier, it is scoring against gold that no longer
describes it, and a green corpus stops meaning anything.
"""

from __future__ import annotations

import os
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
    "conversation",
)


def test_thirteen_categories_declared():
    """Was twelve. EVAL-01 added `conversation`, and the count is pinned again.

    Deliberate change, not a drifted number. The twelve were all single
    questions, which is why 376/376 wrong=0 held throughout the live session
    that produced five P0s: the corpus could not express the shape that broke.
    """
    from bench.corpus import PHASE1_CATEGORIES

    assert len(PHASE1_CATEGORIES) == 13
    assert "conversation" in PHASE1_CATEGORIES


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

    return run_offline(include_expanded=False)


def test_corpus_confidently_wrong_zero(corpus_report):
    assert corpus_report["totals"]["wrong"] == 0


def test_no_answer_silently_became_a_refusal(corpus_report):
    """EVAL-01 — `wrong == 0` was the only assertion here, and abstain was free.

    An answer that quietly turns into a refusal is a regression the customer
    feels, but it scored as neither right nor wrong. The only thing bounding it
    was a rate ceiling of 0.38 over 47 claim items, so three paraphrases flipped
    from answer to abstain on 2026-07-31 and this file stayed green — while five
    live P0 answer defects went uncaught.

    Compared against a recorded baseline of what answers, not against gold.
    "Should this answer?" invites pressure to answer where abstaining is
    correct; "did this change?" is answerable without an opinion.

    Proved able to fail (R-0007): reintroducing the ANS-01 defect makes this
    report exactly ms_exclude_beta#p6, vn_exclude_bare_beta#p3 and
    vn_exclude_full_sku#p3.
    """
    regressed = sorted(
        str(i["id"]) for i in corpus_report["items"] if i.get("regression")
    )

    assert not regressed, (
        "these answered when the baseline was recorded and now abstain: "
        + ", ".join(regressed)
    )


def test_the_regression_check_is_actually_armed(corpus_report):
    """Guard the guard: an empty baseline would pass forever.

    `load_answering_baseline` returns an empty set when the file is missing or
    unparseable, which makes every regression invisible rather than loud — so
    the absence of a baseline has to fail here, not pass quietly.

    CI scores seeds only. The baseline must name every seed that answered this
    run; leftover paraphrase ids from an expanded snapshot are ignored.
    """
    from bench.corpus import load_answering_baseline

    baseline = load_answering_baseline()
    answering = {
        str(i["id"]) for i in corpus_report["items"] if i["outcome"] != "abstain"
    }

    assert baseline, (
        "answering baseline is empty — regenerate with "
        "`python -m bench.corpus --write-baseline`"
    )
    missing = sorted(answering - baseline)
    assert not missing, (
        "baseline is missing answering seeds "
        + ", ".join(missing)
        + " — regenerate with `python -m bench.corpus --write-baseline`"
    )


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


@pytest.mark.corpus_expanded
@pytest.mark.skipif(
    not os.environ.get("CORPUS_EXPANDED"),
    reason="set CORPUS_EXPANDED=1 to score Phase 1b paraphrases",
)
def test_wrong_is_zero_across_the_whole_expansion():
    """Unverified items are still scored — a confident wrong is a defect anywhere."""
    from bench.corpus import run_offline

    report = run_offline(include_expanded=True)
    assert report["corpus"]["expanded_totals"]["wrong"] == 0
    assert report["corpus"]["claim_totals"]["wrong"] == 0


# --- conversation (EVAL-01) --------------------------------------------------
#
# The acceptance clause for EVAL-01 is a proof obligation, not a feature: the
# corpus must go red when one of the five defect shapes in ebd049b..78309fc is
# reintroduced. It could not, for a reason no amount of tuning would have fixed
# - all five defects were the turn AFTER an answer, and every one of the 376
# items was a single question. The category below is the shape, not the count.

#: The five shapes the ticket names, and the commit that fixed each.
_CONVERSATION_SHAPES = {
    "cv_sum_of_them": "ebd049b",
    "cv_count_of_them": "2475f50",
    "cv_reslice_ranking": "2475f50",
    "cv_them_after_a_derived_scalar": "dc86689",
    "cv_paged_total_followup": "78309fc",
}


@pytest.fixture(scope="module")
def conversation_seeds():
    from bench.corpus import load_seeds

    return {s.id: s for s in load_seeds() if s.category == "conversation"}


def test_all_five_reported_shapes_are_in_the_corpus(conversation_seeds):
    missing = sorted(set(_CONVERSATION_SHAPES) - set(conversation_seeds))
    assert not missing, f"conversation shapes with no seed: {missing}"


def test_a_conversation_seed_is_actually_multi_turn(conversation_seeds):
    """The whole point. A `conversation` seed with no prior turn is a question."""
    for seed_id, seed in conversation_seeds.items():
        assert seed.turns, f"{seed_id} has no setup turns, so it is not a conversation"
        assert seed.is_conversation


def test_conversation_gold_is_computable_independently(conversation_seeds):
    """Gold comes from SQL over the warehouse, never from what the engine said.

    A conversation is the easiest place to accidentally write the engine's own
    output down as the requirement, because the answer depends on a prior turn.
    Canonical SQL that reproduces the prior turn as a subquery keeps the gold
    independent - which is the property that lets this category fail.
    """
    for seed_id, seed in conversation_seeds.items():
        assert seed.canonical_sql, f"{seed_id} has no independently computed gold"
        assert seed.key_columns, f"{seed_id} has no key columns, so nothing is compared"


def test_conversation_items_score_and_the_five_seeds_are_correct(corpus_report):
    """The customer-visible gate: rows AND rendered text, on the final turn."""
    items = {i["id"]: i for i in corpus_report["items"] if i["category"] == "conversation"}
    assert items, "the conversation category scored nothing"
    for seed_id in _CONVERSATION_SHAPES:
        assert items[seed_id]["outcome"] == "correct", (
            f"{seed_id}: {items[seed_id]['outcome']} - {items[seed_id].get('detail')}"
        )
        assert items[seed_id]["turns"], f"{seed_id} was scored without its setup turns"


@pytest.mark.parametrize(
    "phrasing",
    ["add them up", "add up those numbers", "what do those five come to", "combine those numbers"],
)
@pytest.mark.xfail(
    strict=True,
    reason=(
        "LIVE GAP, found by the conversation category on the day it was written. "
        "ebd049b keyed the SUM branch on the literal tokens 'sum'/'total', so "
        "every other way of asking to add five numbers still falls through to "
        "the COUNT wrap and answers followup_count = 5 - a count, for a sum, "
        "badged. Same class as the 491. The fix is in "
        "CortexOS/dms/answer_engine.py, which the EVAL-01 lane does not own. "
        "strict=True on purpose: when it is fixed this goes RED, and whoever "
        "fixed it moves these four back into cv_sum_of_them in "
        "bench/corpus/paraphrases_v1.yaml. Pinned, not skipped (R-0002)."
    ),
)
def test_every_way_of_asking_for_a_sum_returns_a_sum(phrasing):
    from CortexOS.dms.answer_engine import answer, clear_session

    sid = f"eval01-gap-{phrasing.replace(' ', '-')}"
    clear_session(sid)
    top = answer("Top 5 selling SKUs by revenue", session_id=sid)
    expected = round(
        sum(float(r["sales_value_myr"]) for r in top["rows"] if r.get("sales_value_myr")), 2
    )

    follow = answer(phrasing, session_id=sid)

    row = (follow.get("rows") or [{}])[0]
    assert "followup_count" not in row, f"{phrasing!r} answered a sum with a count"
    assert row.get("sum_sales_value_myr") == pytest.approx(expected, rel=1e-6)


def test_seeds_only_is_the_default_offline_run():
    import inspect

    from bench.corpus import run_offline

    assert inspect.signature(run_offline).parameters["include_expanded"].default is False


def test_half_b_single_turn_sum_of_ranking_is_in_the_seed_gate(corpus_report):
    """ebd049b half B: the form that names its own subject, no prior turn."""
    item = next(i for i in corpus_report["items"] if i["id"] == "sa_sum_of_a_ranking")
    assert item["outcome"] == "correct", item.get("detail")
    assert not item["turns"]


def test_write_baseline_refuses_a_run_with_wrong_answers():
    from bench.corpus import _write_baseline

    report = {
        "threshold_violations": ["confidently_wrong=1 exceeds floor 0"],
        "items": [{"id": "x", "outcome": "wrong"}],
    }
    with pytest.raises(SystemExit, match="refusing to write"):
        _write_baseline(report, seeds_only=True, live=False)


def test_corpus_modules_do_not_import_dms():
    for rel in (
        "bench/corpus.py",
        "bench/live_probe.py",
        "bench/envelope.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "from dms_executor" not in text, rel
        assert "import dms_executor" not in text, rel
        assert "D:\\DMS" not in text, rel
        assert "packages/executor" not in text, rel
        assert "127.0.0.1:8090" not in text, rel
