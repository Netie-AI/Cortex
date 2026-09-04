"""C7-04 — held-out corpus is not metrics.yaml paraphrases; envelope harness."""

from __future__ import annotations

from pathlib import Path

import yaml

from bench.heldout import (
    HELDOUT_PATH,
    HeldoutItem,
    assert_not_team_paraphrases,
    load_heldout,
    overlap_hits,
    score_envelope,
    score_fixture,
    team_dev_phrases,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "c7_heldout" / "tiny.yaml"
METRICS = ROOT / "packs" / "dms" / "semantic" / "metrics.yaml"


def test_harness_scores_tiny_fixture_envelopes():
    """CI gate: invoke the harness on canned envelopes so a bad scorer fails."""
    report = score_fixture(FIXTURE)
    by_id = {row["id"]: row["outcome"] for row in report["results"]}
    assert by_id["fx_match"] == "correct"
    assert by_id["fx_scalar"] == "correct"
    assert by_id["fx_empty_success"] == "incorrect"
    assert by_id["fx_empty_gold"] == "incorrect"
    assert by_id["fx_blank_text"] == "incorrect"
    assert by_id["fx_sql_only"] == "incorrect"
    assert by_id["fx_abstain_ok"] == "abstained"
    assert by_id["fx_abstain_leaked"] == "incorrect"
    assert by_id["fx_answerable_abstain"] == "abstained"
    assert report["totals"]["incorrect"] == 5
    assert report["totals"]["correct"] == 2
    assert report["totals"]["abstained"] == 2


def test_empty_rows_never_correct_even_with_matching_sql():
    item = HeldoutItem(
        id="sku_beta",
        split="sql",
        provenance="bird_style",
        question="excluding BETA nested ranking",
        expect="correct_rows",
        key_columns=["sku"],
        expected_rows=[{"sku": "SKU-ALPHA"}],
    )
    env = {
        "answer": "No matching SKUs.",
        "rows": [],
        "badge": "governed_metric",
        "route": "sql",
        "sql_used": "SELECT sku FROM transactions WHERE sku NOT IN ('SKU-BETA')",
    }
    assert score_envelope(item, env).outcome == "incorrect"


def test_sql_used_alone_does_not_score_correct():
    item = HeldoutItem(
        id="sql_only",
        split="sql",
        provenance="spider_style",
        question="nested join example",
        expect="correct_rows",
        key_columns=["sku"],
        expected_rows=[{"sku": "SKU-ALPHA"}],
    )
    env = {
        "answer": "done",
        "rows": [{"sku": "SKU-WRONG"}],
        "badge": "L2_VALIDATED",
        "route": "sql",
        "sql_used": "SELECT sku FROM inventory WHERE sku = 'SKU-ALPHA'",
    }
    assert score_envelope(item, env).outcome == "incorrect"


def test_frozen_heldout_shape_and_provenance():
    data = yaml.safe_load(HELDOUT_PATH.read_text(encoding="utf-8"))
    assert "paraphrases" not in data
    assert data.get("split") == "heldout"
    prov = data.get("provenance") or {}
    blob = str(prov)
    assert "BIRD" in blob and "Spider" in blob
    assert "different-model" in blob or "different_model" in blob
    items = load_heldout()
    assert 20 <= len(items) <= 80
    sql_n = sum(1 for i in items if i.split == "sql")
    abs_n = sum(1 for i in items if i.split == "must_abstain")
    assert sql_n >= 8 and abs_n >= 8
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids))
    for item in items:
        if item.split == "sql":
            assert item.provenance in {"bird_style", "spider_style"}
            assert item.expect == "correct_rows"
            assert item.canonical_sql
            assert item.key_columns
        else:
            assert item.split == "must_abstain"
            assert item.provenance == "different_model_abstain"
            assert item.expect == "abstain"
            assert not item.canonical_sql


def test_frozen_heldout_is_not_metrics_or_golden_paraphrase():
    assert_not_team_paraphrases()


def test_overlap_guard_fails_if_metrics_synonyms_are_swapped_in():
    metrics = yaml.safe_load(METRICS.read_text(encoding="utf-8"))
    synonyms = []
    for metric in metrics.get("metrics") or []:
        synonyms.extend(str(s) for s in (metric.get("synonyms") or []) if s)
    assert synonyms, "metrics.yaml has no synonyms to swap"
    hits = overlap_hits(synonyms[:8])
    assert hits, "overlap guard would stay green if the held-out set became metrics synonyms"


def test_overlap_guard_fails_if_golden_paraphrase_file_is_swapped_in():
    data = yaml.safe_load(
        (ROOT / "bench" / "golden" / "dms_paraphrase_v1.yaml").read_text(encoding="utf-8")
    )
    phrases: list[str] = []
    for group in (data.get("paraphrases") or {}).values():
        phrases.extend(str(p) for p in (group or []))
    assert phrases
    hits = overlap_hits(phrases[:12])
    assert hits, "overlap guard would stay green if dms_paraphrase_v1.yaml were the held-out set"


def test_team_dev_phrases_include_metrics_and_paraphrase_files():
    phrases = team_dev_phrases()
    sources = {src for src, _ in phrases}
    assert any(s.startswith("metrics.yaml:") for s in sources)
    assert any(s.startswith("dms_golden_v1.yaml:") for s in sources)
    assert any(s.startswith("dms_paraphrase_v1.yaml:") for s in sources)


def test_dms_envelope_spelling_is_accepted():
    item = HeldoutItem(
        id="dms_env",
        split="sql",
        provenance="bird_style",
        question="envelope spelling",
        expect="correct_rows",
        match="scalar",
        key_columns=["sku_count"],
        expected_rows=[{"sku_count": 4}],
    )
    env = {
        "text": "The nested count is 4.",
        "values": [{"sku_count": 4}],
        "badge": "ABSTAIN",
        "abstained": True,
        "route": "needs_clarification",
    }
    # Refusal with no... wait this has values. G-env: refusal + rows => incorrect.
    assert score_envelope(item, env).outcome == "incorrect"

    env_ok = {
        "text": "The nested count is 4.",
        "values": [{"sku_count": 4}],
        "badge": "L1_GOVERNED_METRIC",
        "abstained": False,
        "route": "sql",
    }
    assert score_envelope(item, env_ok).outcome == "correct"


def test_score_engine_uses_ask_callable_not_live_l2():
    import os

    from bench.heldout import score_engine

    item = HeldoutItem(
        id="must_abs",
        split="must_abstain",
        provenance="different_model_abstain",
        question="esg plus berlin weather 1997",
        expect="abstain",
    )
    os.environ.pop("DMS_L2_ENABLED", None)
    seen: dict[str, str | None] = {}

    def ask(question: str) -> dict:
        seen["flag"] = os.environ.get("DMS_L2_ENABLED")
        del question
        return {
            "answer": "I can't answer that.",
            "rows": [],
            "badge": "abstain",
            "route": "needs_clarification",
        }

    report = score_engine(
        [item], ask=ask, enable_l2=True, count_shadow=False
    )
    assert seen["flag"] == "1"
    assert os.environ.get("DMS_L2_ENABLED") is None
    assert report["totals"]["abstained"] == 1
    assert report["gates"]["g_abs"] is True
    assert report["cutover"] is False
    assert report["l2_enabled_for_run"] is True


def test_score_engine_does_not_claim_cutover_when_g4_empty_success():
    from bench.heldout import score_engine

    item = HeldoutItem(
        id="sql_empty",
        split="sql",
        provenance="bird_style",
        question="how many skus",
        expect="correct_rows",
        match="scalar",
        key_columns=["n"],
        expected_rows=[{"n": 4}],
    )

    def ask(question: str) -> dict:
        del question
        return {
            "answer": "none",
            "rows": [],
            "badge": "L2_VALIDATED",
            "route": "sql",
        }

    report = score_engine([item], ask=ask, count_shadow=False)
    assert report["totals"]["incorrect"] == 1
    assert report["gates"]["g_env"] is False
    assert report["cutover"] is False
