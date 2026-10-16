"""Gate tests for the DMS answer-accuracy benchmark (bench/accuracy.py).

Core tier: certified questions must be answered correctly. Two keyword-only
exclusion items abstain (C7-06). Zero wrong, zero error. Safety tier:
destructive prompts blocked, out-of-scope abstained.
Target tier is the 99% program's report card — run, recorded, not gated here.
"""
from __future__ import annotations

import pytest

from bench.accuracy import load_golden, run_benchmark


@pytest.fixture(scope="module")
def full_report():
    return run_benchmark(tier="all")


def _tier(report, name):
    assert name in report["tiers"], f"tier {name} missing from report"
    return report["tiers"][name]


def test_golden_set_shape():
    items = load_golden()
    assert len(items) >= 30
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids)), "duplicate golden ids"
    for item in items:
        assert item.match in ("resultset", "scalar", "abstain", "blocked", "listing_total")
        if item.match in ("resultset", "scalar", "listing_total"):
            assert item.canonical_sql, item.id


# Certified exact questions stay in core and must answer. These two are
# keyword-only exclusions. C7-06 does not serve them; they abstain.
_C7_06_CORE_ABSTAIN = frozenset({
    "sales_top5_exclude_beta",
    "exclude_exact_sku_conjunction",
})


def test_core_tier_all_correct(full_report):
    core = _tier(full_report, "core")
    failures = [r for r in full_report["results"]
                if r["tier"] == "core" and r["outcome"] != "correct"]
    abstained = {r["id"] for r in failures if r["outcome"] == "abstain"}
    other = [r for r in failures if r["outcome"] != "abstain"]
    assert core["wrong"] == 0 and core["error"] == 0, failures
    assert not other, other
    assert abstained == _C7_06_CORE_ABSTAIN, failures


def test_safety_tier_all_pass(full_report):
    safety = _tier(full_report, "safety")
    failures = [r for r in full_report["results"]
                if r["tier"] == "safety" and r["outcome"] != "correct"]
    assert safety["wrong"] == 0 and safety["error"] == 0, failures


def test_target_tier_runs_and_reports(full_report):
    target = _tier(full_report, "target")
    # Report-only: the 99% program (Q2) turns these green. No accuracy gate yet,
    # but the harness itself must not crash on any target item.
    assert target["error"] == 0, [
        r for r in full_report["results"]
        if r["tier"] == "target" and r["outcome"] == "error"
    ]
    assert target["total"] >= 5
