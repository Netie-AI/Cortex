"""LANES-V1 frozen labels (LANE-ROUTER-01 PR-L1): counts, provenance, gold, freeze.

The lane router may only be turned on after a measurement over these labels
(plan section 6). A label set that is too small, that leaks held-out items into
the tuning split, or whose gold does not hold cannot support that decision, so
each property is pinned here.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest

from bench.heldout import load_heldout
from bench.lanes import (
    DOCS_DIR,
    LANES_PATH,
    LANES_SHA_PATH,
    LaneSet,
    fact_in_text,
    file_sha256,
    load_corpus,
    load_lanes,
    normalize_question,
    review_problems,
)
from tests.dms.test_destructive_intent import (
    ANALYTICS_QUESTIONS,
    DOCUMENT_QUESTIONS,
    MUST_BLOCK,
    MUST_NOT_BLOCK,
)
from tests.invariants.test_model_egress_chokepoint import PROVIDER_HOST

KEY_SHAPED = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY"
    r"|(?i:(?:api[_-]?key|secret|bearer|token)\s*[:=]\s*\S{12,})"
)


@pytest.fixture(scope="module")
def lanes() -> LaneSet:
    return load_lanes()


def _heldout(lanes: LaneSet):
    return lanes.split("heldout")


def test_held_out_counts(lanes: LaneSet) -> None:
    held = _heldout(lanes)
    non_doc = {normalize_question(i.question) for i in held if not i.is_document}
    kinds = Counter(i.kind for i in held)
    assert len(non_doc) >= 300, len(non_doc)
    assert kinds["doc_hit"] + kinds["doc_miss"] >= 120, kinds
    assert kinds["doc_hit"] >= 80, kinds
    assert kinds["doc_miss"] >= 40, kinds
    assert kinds["destructive"] >= 40, kinds
    assert sum(1 for i in held if i.has_data_gold) >= 100


def test_questions_are_distinct(lanes: LaneSet) -> None:
    seen = Counter(normalize_question(i.question) for i in lanes.items)
    assert [q for q, n in seen.items() if n > 1] == []


def test_every_c7_heldout_item_is_pinned_held_out(lanes: LaneSet) -> None:
    by_source = {i.source: i for i in lanes.items if i.source}
    c7 = [f"c7_heldout_v1/{h.id}" for h in load_heldout()]
    assert len(c7) == 28
    missing = [s for s in c7 if s not in by_source]
    assert missing == []
    assert [s for s in c7 if by_source[s].split != "heldout"] == []


def test_destructive_intent_lists_are_present(lanes: LaneSet) -> None:
    questions = {i.question: i for i in lanes.items}
    for q in [*DOCUMENT_QUESTIONS, *ANALYTICS_QUESTIONS, *MUST_NOT_BLOCK]:
        assert q in questions, q
    for q in MUST_BLOCK:
        item = questions[q]
        assert item.kind == "destructive" and item.must_block and item.split == "heldout", q
    for q in DOCUMENT_QUESTIONS:
        assert questions[q].kind == "doc_hit", q
    for q in [*ANALYTICS_QUESTIONS, *MUST_NOT_BLOCK]:
        assert not questions[q].is_document and questions[q].kind != "destructive", q


def test_every_gold_doc_exists_and_holds_its_facts(lanes: LaneSet) -> None:
    corpus = load_corpus()
    assert len(corpus) >= 10
    for item in lanes.items:
        if item.kind != "doc_hit":
            assert item.doc is None and not item.facts, item.id
            continue
        assert item.doc in corpus, item.id
        assert item.facts, item.id
        for fact in item.facts:
            assert fact_in_text(fact, corpus[item.doc]), (item.id, fact)


def test_labels_are_consistent(lanes: LaneSet) -> None:
    for item in lanes.items:
        if item.kind == "doc_hit":
            assert item.lane == "documents" and item.accept == ("document",), item.id
        else:
            # Only a doc_hit may ever be served a document (Stage C bar C3).
            assert "document" not in item.accept, item.id
        if not item.is_document:
            assert item.lane == "other", item.id
        if item.kind == "destructive":
            assert item.accept == ("abstain",), item.id
        if item.has_data_gold:
            assert item.kind == "data" and item.accept == ("rows",), item.id


def test_no_paraphrase_group_straddles_the_splits(lanes: LaneSet) -> None:
    splits: dict[str, set[str]] = {}
    for item in lanes.items:
        if item.group:
            splits.setdefault(item.group, set()).add(item.split)
    assert splits, "no paraphrase groups"
    assert {g: s for g, s in splits.items() if len(s) > 1} == {}


def test_sha256_matches_the_frozen_file() -> None:
    recorded = LANES_SHA_PATH.read_text(encoding="utf-8").split()
    assert recorded[1:] == ["lanes_v1.yaml"]
    assert recorded[0] == file_sha256(LANES_PATH)


def test_review_block_is_well_formed(lanes: LaneSet) -> None:
    assert review_problems(lanes.header) == []


def test_label_review_is_recorded(lanes: LaneSet) -> None:
    """PR-L1's merge condition: a blind relabel by a different model family."""
    status = lanes.header["review"]["status"]
    if status == "pending":
        pytest.skip("label review pending: PR-L1 merges only once review.status is complete")
    assert status == "complete"
    assert review_problems(lanes.header) == []


def test_no_key_shaped_strings_or_provider_hosts() -> None:
    files = [LANES_PATH, LANES_SHA_PATH, *sorted(DOCS_DIR.glob("*.txt"))]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not KEY_SHAPED.search(text), path.name
        assert not PROVIDER_HOST.search(text), path.name


def test_held_out_data_gold_runs_and_returns_rows(lanes: LaneSet) -> None:
    """Empty gold makes every answer WRONG (bench.heldout rule), so it is refused here."""
    from bench.accuracy import _ensure_db_loaded, _run_canonical

    _ensure_db_loaded()
    for item in lanes.items:
        if not item.has_data_gold or item.canonical_sql is None:
            continue
        rows = _run_canonical(item.canonical_sql)
        assert rows, item.id
        assert all(k in rows[0] for k in item.key_columns), item.id
        if item.match == "scalar":
            assert len(rows) == 1, item.id


# ── must-fail: the checks above can go red ───────────────────────────────────


def test_review_problems_must_fail_on_same_family_labeler() -> None:
    header = {
        "review": {
            "status": "complete",
            "writer": {"family": "claude"},
            "labeler": "relabel run 1",
            "labeler_family": "Claude",
            "kappa": 0.91,
            "adjudication": [],
            "dropped": [],
            "founder_spot_check": "10% checked",
        }
    }
    assert review_problems(header) == ["labeler family 'claude' is the writer's family"]
    header["review"]["labeler_family"] = "gpt"
    assert review_problems(header) == []
    header["review"]["kappa"] = None
    assert review_problems(header) == ["review.kappa None is not a number in [-1, 1]"]
    header["review"]["kappa"] = 0.9
    header["review"]["founder_spot_check"] = None
    assert review_problems(header) == ["review.founder_spot_check is not recorded"]
    assert review_problems({}) == ["no review block"]


def test_load_lanes_must_fail_on_malformed_items(tmp_path) -> None:
    base = (
        "items:\n"
        "- {id: a, split: heldout, kind: data, lane: other, accept: [rows], question: q}\n"
    )
    path = tmp_path / "lanes.yaml"
    path.write_text(base, encoding="utf-8")
    assert load_lanes(path).items[0].id == "a"
    for bad, needle in (
        (base.replace("heldout", "dev"), "split"),
        (base.replace("kind: data", "kind: clarify"), "kind"),
        (base.replace("lane: other", "lane: general"), "lane"),
        (base.replace("[rows]", "[answer]"), "accept"),
        (base + base.split("\n", 1)[1], "duplicate"),
    ):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError, match=needle):
            load_lanes(path)


def test_key_and_host_scans_must_fail_on_a_plant() -> None:
    assert KEY_SHAPED.search("api_key: abcdefghijklmnop")
    assert KEY_SHAPED.search("sk-" + "a" * 24)
    assert PROVIDER_HOST.search("https://api.openai.com/v1")
