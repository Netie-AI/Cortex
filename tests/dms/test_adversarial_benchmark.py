"""C10 — adversarial category gates (wrong==0 on gated categories)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "bench" / "golden" / "adversarial_baseline.json"


@pytest.fixture(scope="module")
def adversarial_report():
    from bench.adversarial import run_benchmark

    try:
        return run_benchmark()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "being used by another process" in msg or "Cannot open file" in msg:
            pytest.skip(f"warehouse locked by live Cortex process: {msg[:160]}")
        raise


@pytest.fixture(scope="module")
def baseline():
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_adversarial_wrong_zero_on_gated(adversarial_report, baseline):
    assert baseline.get("wrong_must_be_zero") is True
    by_cat = adversarial_report["by_category"]
    for cat in baseline.get("gated_categories") or []:
        summary = by_cat.get(cat) or {"wrong": 0, "total": 0}
        wrong = int(summary.get("wrong") or 0)
        assert wrong == 0, f"{cat}: confidently wrong={wrong} items={summary}"


def test_value_normalization_category_present(adversarial_report):
    by_cat = adversarial_report["by_category"]
    assert "value_normalization" in by_cat
    assert by_cat["value_normalization"]["total"] >= 1
    assert by_cat["value_normalization"]["wrong"] == 0


def test_eleven_categories_declared():
    from bench.adversarial import CATEGORIES

    assert len(CATEGORIES) == 11
    assert "value_normalization" in CATEGORIES
