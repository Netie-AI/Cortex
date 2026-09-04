"""C7-04 — held-out corpus is not metrics.yaml paraphrases; envelope harness."""

from __future__ import annotations

import json
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


def test_score_engine_records_ask_crash_as_incorrect():
    from bench.heldout import score_engine

    item = HeldoutItem(
        id="crash",
        split="sql",
        provenance="bird_style",
        question="how many skus",
        expect="correct_rows",
        key_columns=["n"],
        expected_rows=[{"n": 4}],
    )

    def ask(question: str) -> dict:
        del question
        raise RuntimeError("warehouse missing")

    report = score_engine([item], ask=ask, count_shadow=False)
    assert report["totals"]["incorrect"] == 1
    assert "RuntimeError" in report["results"][0]["detail"]


def test_summarize_shadow_reports_l1_only_vs_l2_only(tmp_path: Path):
    from bench.heldout import summarize_shadow

    item = HeldoutItem(
        id="sku_n",
        split="sql",
        provenance="bird_style",
        question="how many skus in inventory now",
        expect="correct_rows",
        match="scalar",
        key_columns=["n"],
        expected_rows=[{"n": 4}],
    )
    path = tmp_path / "l2_shadow.jsonl"
    recs = [
        {
            "question": item.question,
            "served_layer": "governed_metric",
            "served_badge": "ok",
            "served_row_count": 1,
            "served_values": [{"n": 4}],
            "l2_sql": None,
            "l2_refusal_type": "leave-machine gate denied",
            "l2_row_count": None,
            "l2_values": None,
            "agree": False,
        },
        {
            "question": item.question,
            "served_layer": "abstain",
            "served_badge": "abstain",
            "served_row_count": 0,
            "served_values": [],
            "l2_sql": "SELECT 4 AS n",
            "l2_refusal_type": None,
            "l2_row_count": 1,
            "l2_values": [{"n": 4}],
            "agree": False,
        },
    ]
    path.write_text("\n".join(__import__("json").dumps(r) for r in recs) + "\n", encoding="utf-8")
    out = summarize_shadow(path, items=[item])
    assert out["n_lines"] == 2
    assert out["n_unique"] == 1
    assert out["n_l2_sql"] == 1
    assert out["l1_only_correct"] == 1
    assert out["l2_only_correct"] == 1


def test_shadow_target_honors_env_path(tmp_path: Path, monkeypatch):
    from bench.heldout import _shadow_target, summarize_shadow

    dest = tmp_path / "gsh.jsonl"
    dest.write_text('{"question": "how many skus in inventory now", "l2_sql": "SELECT 1"}\n', encoding="utf-8")
    monkeypatch.setenv("DMS_L2_SHADOW_PATH", str(dest))
    assert _shadow_target() == dest
    out = summarize_shadow()
    assert out["n_lines"] == 1
    assert out["n_l2_sql"] == 1


def test_collect_dev_questions_are_operator_phrases():
    from bench.heldout import collect_dev_questions

    qs = collect_dev_questions()
    assert len(qs) >= 200
    lowered = {q.lower() for q in qs}
    assert "sku_count" not in lowered
    assert any("how many" in q.lower() for q in qs)
    assert any("berapa banyak sku" in q.lower() for q in qs)


def test_malay_berapa_banyak_sku_routes_to_sku_count():
    from CortexOS.dms.answer_engine import route_to_metric

    plan = route_to_metric("boss, berapa banyak SKU kita ada sekarang?")
    assert plan is not None
    assert plan.metric_id == "sku_count"


def test_replay_shadow_restores_l2_env(tmp_path: Path, monkeypatch):
    import os

    from bench.heldout import replay_shadow

    monkeypatch.setenv("DMS_L2_ENABLED", "0")
    monkeypatch.delenv("DMS_L2_SHADOW", raising=False)
    monkeypatch.delenv("DMS_L2_SHADOW_PATH", raising=False)
    path = tmp_path / "shadow.jsonl"
    seen: list[str] = []

    def ask(question: str) -> dict:
        seen.append(os.environ.get("DMS_L2_SHADOW") or "")
        assert os.environ.get("DMS_L2_ENABLED") is None
        rec = {
            "question": question,
            "served_layer": "abstain",
            "served_badge": "abstain",
            "served_row_count": 0,
            "served_values": [],
            "l2_sql": None,
            "l2_refusal_type": "not_enabled",
            "agree": True,
        }
        path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        return {"answer": "no", "rows": [], "badge": "abstain", "route": "abstain"}

    out = replay_shadow(["how many skus in stock"], shadow_path=path, ask=ask)
    assert seen == ["1"]
    assert os.environ.get("DMS_L2_ENABLED") == "0"
    assert os.environ.get("DMS_L2_SHADOW") is None
    assert out["n_lines"] == 1
    assert out["replayed"] == 1

    skipped = replay_shadow(
        ["first", "second"], shadow_path=path, ask=ask, offset=1, limit=1
    )
    assert skipped["replayed"] == 1
