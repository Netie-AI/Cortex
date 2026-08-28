"""C10-min — paraphrase adversarial ratchet: wrong must stay 0; robustness floor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "bench" / "golden" / "paraphrase_baseline.json"


@pytest.fixture(scope="module")
def paraphrase_report():
    from bench.paraphrase import run_benchmark

    return run_benchmark()


def test_paraphrase_zero_confidently_wrong(paraphrase_report):
    totals = paraphrase_report["totals"]
    assert totals["wrong"] == 0, (
        f"confidently wrong paraphrases must stay 0; got {totals['wrong']}"
    )
    assert totals["error"] == 0


def test_paraphrase_robustness_floor(paraphrase_report):
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    floor = float(baseline["robustness_floor"])
    got = float(paraphrase_report["totals"]["robustness"])
    assert got + 1e-9 >= floor, (
        f"robustness {got} fell below committed floor {floor} — "
        "fix regressions or raise the floor only after intentional gains"
    )
