"""RSF-07 eval corpus. R-0003 different-run verify of RSF-04 paths.

Scores live ``run_rsf`` output against frozen gold. Precision-on-answered:
one CERTIFIED that does not match gold is WRONG and fails the suite.
Does not reimplement the orchestrator. Does not claim epic COMPLETE.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from CortexOS.execution.rsf_operate import render_audit_operate
from CortexOS.execution.rsf_orchestrator import BAKEOFF_SURFACES, run_rsf
from CortexOS.rsf import RSF_STAGES, RSF_STATUSES

CORPUS_DIR = Path(__file__).resolve().parent / "rsf_corpus"
CASES_PATH = CORPUS_DIR / "cases.json"
ARTIFACTS_DIR = CORPUS_DIR / "artifacts"
POISON_PATH = CORPUS_DIR / "poison_invent_green.json"

OUTCOME_KEYS = ("total", "correct", "wrong", "abstain", "error", "regression")


def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    blob = json.loads((path or CASES_PATH).read_text(encoding="utf-8"))
    cases = blob.get("cases") if isinstance(blob, dict) else blob
    if not isinstance(cases, list):
        raise ValueError("RSF eval corpus must be a list of cases")
    out: list[dict[str, Any]] = []
    for item in cases:
        if isinstance(item, dict) and item.get("id"):
            out.append(item)
    return out


def load_artifact_run(name: str) -> dict[str, Any]:
    path = ARTIFACTS_DIR / f"{name}.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(blob, dict):
        raise ValueError(f"artifact {name} must be an object")
    return blob


def load_poison_run() -> dict[str, Any]:
    blob = json.loads(POISON_PATH.read_text(encoding="utf-8"))
    if not isinstance(blob, dict):
        raise ValueError("poison fixture must be an object")
    return blob


def score_stage(
    pred: Mapping[str, Any] | None,
    gold_status: str,
    gold_chosen: str | None,
) -> str:
    """Precision-on-answered: CERTIFIED is an answer. A mismatch is WRONG."""
    if not isinstance(pred, Mapping):
        return "error"
    pred_status = pred.get("status")
    pred_chosen = pred.get("chosen_option")
    if pred_chosen is not None and not isinstance(pred_chosen, str):
        pred_chosen = None
    if pred_status == "CERTIFIED":
        if gold_status == "CERTIFIED" and pred_chosen == gold_chosen:
            return "correct"
        return "wrong"
    if pred_status not in RSF_STATUSES:
        return "error"
    if pred_chosen is not None:
        return "wrong"
    if gold_status == "CERTIFIED":
        return "abstain"
    if pred_status == gold_status:
        return "correct"
    if gold_status in {"ABSTAIN", "REFUSE"} and pred_status in {"ABSTAIN", "REFUSE"}:
        return "correct"
    return "error"


def score_trace(artifacts: Sequence[Mapping[str, Any]] | None, gold: Mapping[str, Any]) -> dict[str, Any]:
    rows = [a for a in (artifacts or []) if isinstance(a, Mapping)]
    by_stage = {str(a.get("stage") or ""): a for a in rows}
    gold_statuses = list(gold.get("statuses") or [])
    gold_chosen = gold.get("chosen") if isinstance(gold.get("chosen"), Mapping) else {}
    stages: list[dict[str, Any]] = []
    counts = {"correct": 0, "wrong": 0, "abstain": 0, "error": 0}
    for i, stage in enumerate(RSF_STAGES):
        want_status = gold_statuses[i] if i < len(gold_statuses) else "ABSTAIN"
        want_chosen = gold_chosen.get(stage)
        if want_chosen is not None and not isinstance(want_chosen, str):
            want_chosen = None
        outcome = score_stage(by_stage.get(stage), want_status, want_chosen)
        counts[outcome] += 1
        pred = by_stage.get(stage) or {}
        stages.append(
            {
                "stage": stage,
                "outcome": outcome,
                "pred_status": pred.get("status"),
                "pred_chosen": pred.get("chosen_option"),
                "gold_status": want_status,
                "gold_chosen": want_chosen,
            }
        )
    return {"stages": stages, **counts}


def threshold_violations(report: Mapping[str, Any]) -> list[str]:
    totals = report.get("totals") if isinstance(report.get("totals"), Mapping) else {}
    wrong = int(totals.get("wrong") or 0)
    regression = int(totals.get("regression") or 0)
    violations: list[str] = []
    if wrong > 0:
        violations.append(f"confidently_wrong={wrong} exceeds floor 0")
    if regression > 0:
        violations.append(
            f"items that used to match gold now miss ({regression}): "
            + ", ".join(report.get("regressions") or [])
        )
    return violations


def _empty_totals() -> dict[str, int]:
    return {key: 0 for key in OUTCOME_KEYS}


def run_case(case: Mapping[str, Any]) -> dict[str, Any]:
    question = str(case.get("question") or "")
    destination = str(case.get("destination") or "freeroute")
    proposals = case.get("proposals") if isinstance(case.get("proposals"), Mapping) else None
    run = run_rsf(question, proposals=proposals, destination=destination)
    gold = case.get("gold") if isinstance(case.get("gold"), Mapping) else {}
    scored = score_trace(run.get("artifacts") if isinstance(run.get("artifacts"), list) else [], gold)
    expect_ok = bool(gold.get("ok"))
    regression = expect_ok and scored["wrong"] == 0 and (
        scored["abstain"] > 0 or scored["error"] > 0 or run.get("ok") is not True
    )
    views = render_audit_operate({**run, "destination": destination})
    return {
        "id": case.get("id"),
        "surface": case.get("surface"),
        "path": case.get("path"),
        "ok": run.get("ok") is True,
        "engine": run.get("engine"),
        "wrong": scored["wrong"],
        "abstain": scored["abstain"],
        "error": scored["error"],
        "correct": scored["correct"],
        "regression": regression,
        "stages": scored["stages"],
        "run": run,
        "audit": views["audit"],
        "operate": views["operate"],
    }


def run_eval(cases: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    items = list(cases) if cases is not None else load_cases()
    totals = _empty_totals()
    results: list[dict[str, Any]] = []
    regressions: list[str] = []
    for case in items:
        row = run_case(case)
        results.append(row)
        totals["total"] += 1
        if row["wrong"] > 0:
            totals["wrong"] += 1
        elif row["error"] > 0:
            totals["error"] += 1
        elif row["regression"]:
            totals["regression"] += 1
            regressions.append(str(row["id"]))
        elif row["abstain"] > 0 and not (case.get("gold") or {}).get("ok"):
            totals["abstain"] += 1
        else:
            totals["correct"] += 1
    report = {
        "totals": totals,
        "results": results,
        "regressions": regressions,
        "surfaces": sorted({str(c.get("surface") or "") for c in items if c.get("surface")}),
        "bakeoff_surfaces": list(BAKEOFF_SURFACES),
    }
    report["violations"] = threshold_violations(report)
    report["pass"] = not report["violations"]
    return report


__all__ = [
    "ARTIFACTS_DIR",
    "CASES_PATH",
    "CORPUS_DIR",
    "POISON_PATH",
    "load_artifact_run",
    "load_cases",
    "load_poison_run",
    "run_case",
    "run_eval",
    "score_stage",
    "score_trace",
    "threshold_violations",
]
