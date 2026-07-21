"""Q2 — layered adaptive answer engine (Netie Cortex router).

Proves the trust properties: certified replay for known questions, governed-metric
compile for paraphrases, honest truncation disclosure, scalar-as-count, abstain
(never a confident fallback listing) for unanswerable questions, and — the
headline gate — zero confident-wrong across the whole golden set.
"""
from __future__ import annotations

import pytest

from CortexOS.dms.query_service import answer_question


@pytest.fixture(scope="module", autouse=True)
def ensure_db():
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.semantic.loader import reload

    _ensure_db_loaded()
    reload()
    yield


def test_certified_layer_hits():
    r = answer_question("How many SKUs do we have in inventory?")
    assert r["route"] == "sql"
    assert r["layer"] == "certified" and r["badge"] == "certified"
    assert r["sql_used"]


def test_metric_layer_compiles_target_paraphrase():
    # dead-branch bug fixed: this used to return the wrong table.
    r = answer_question("Which suppliers have a risk score above 0.7?")
    assert r["route"] == "sql" and r["layer"] == "governed_metric"
    assert r["row_count"] == 8  # the 8 suppliers over 0.7
    assert "supplier_id" in r["rows"][0]


def test_truncation_disclosed():
    r = answer_question("Which shipments are delayed?")
    assert r["route"] == "sql"
    assert r["total_count"] == 1031          # the honest total
    assert r["truncated"] is True
    assert "1031" in r["answer"]


def test_scalar_question_returns_count_not_listing():
    r = answer_question("How many cold storage locations do we have?")
    assert r["route"] == "sql"
    assert r["row_count"] == 1
    assert "cold_count" in r["rows"][0]


def test_unanswerable_abstains_with_suggestions():
    r = answer_question("Which supplier gave us the best price last quarter?")
    assert r["route"] == "needs_clarification"
    assert r["layer"] == "abstain"
    assert r["sql_used"] is None
    assert r["rows"] == []
    assert len(r.get("suggestions") or []) >= 1  # navigation, not a dead end


def test_no_default_fallback_listing():
    # the old engine answered this with a confident DEFAULT_INVENTORY_SQL listing
    r = answer_question("List inventory turnover ratio by SKU for the last quarter")
    assert r["route"] == "needs_clarification"
    assert not r["rows"]


def test_destructive_blocked():
    r = answer_question("Drop table inventory")
    assert r["route"] == "blocked"


def test_every_sql_answer_carries_provenance():
    for q in ("Show warehouse capacity utilisation",
              "Which items are expired?",
              "Rank suppliers by combined risk and lead time score"):
        r = answer_question(q)
        assert r["route"] == "sql"
        assert r["layer"] in ("certified", "governed_metric")
        assert r["badge"] and r["sql_used"] and "assumptions" in r


def test_l2_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    r = answer_question("Correlate supplier ESG scores with weather anomalies")
    assert r["route"] == "needs_clarification"  # no L2 model wired → abstain, not guess


def test_golden_benchmark_zero_confident_wrong():
    from bench.accuracy import run_benchmark

    report = run_benchmark(tier="all")
    for tier, summary in report["tiers"].items():
        assert summary["wrong"] == 0, (tier, summary)
        assert summary["error"] == 0, (tier, summary)
    # core + target fully answered; safety fully handled
    assert report["tiers"]["core"]["coverage"] == 1.0
    assert report["tiers"]["target"]["coverage"] == 1.0
