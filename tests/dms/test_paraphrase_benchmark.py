"""C10-min — paraphrase adversarial ratchet: wrong must stay 0; robustness floor."""

from __future__ import annotations

import pytest


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
    """#412 retired keyword-cascade paraphrase serving.

    The committed floor measured how often a different wording still compiled
    the parent's SQL. A paraphrase is not the metric's declared question, so
    it reconfirms. Wrong stays 0. A correct row is allowed only when the
    parent already expected a refusal (abstain, blocked, or reconfirm).
    Teaching the cascade a new phrasing fails the served-row check.
    """
    totals = paraphrase_report["totals"]
    assert totals["wrong"] == 0
    assert totals["error"] == 0
    assert totals["total"] > 0
    # A paraphrase of an answerable question reconfirms. The only correct
    # rows here are paraphrases of a parent that already expected a refusal.
    from bench.accuracy import load_golden

    parents = {item.id: item for item in load_golden()}
    served = [
        row
        for row in paraphrase_report["results"]
        if row["outcome"] == "correct"
        and parents[row["golden_id"]].match not in {"abstain", "blocked", "reconfirm"}
    ]
    assert served == [], served
