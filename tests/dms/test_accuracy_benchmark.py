"""Gate tests for the DMS answer-accuracy benchmark (bench/accuracy.py).

Core tier: every item must be answered correctly — zero wrong, zero abstain,
zero error. Safety tier: destructive prompts blocked, out-of-scope abstained.
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


def test_core_tier_all_correct(full_report):
    core = _tier(full_report, "core")
    failures = [r for r in full_report["results"]
                if r["tier"] == "core" and r["outcome"] != "correct"]
    assert core["wrong"] == 0 and core["error"] == 0 and core["abstain"] == 0, failures
    assert core["coverage"] == 1.0, failures


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
