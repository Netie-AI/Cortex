"""C7-06 — route_to_metric is not the serve chooser until L2 beats L1.

Cutover flags stay honest: a flag, DMS_L2_ENABLED, or JSON cutover:true
cannot invent-green. Keyword slot helpers remain. The 25 _metric_plan
branches are not deleted in this ticket.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from CortexOS.dms.c7_cutover import (
    cascade_retired,
    cutover_flags,
    l2_replaces_l1,
)

L1_Q = "Which suppliers have a risk score above 0.7?"
L0_Q = "How many SKUs do we have in inventory?"


def _l1_report(**overrides) -> dict:
    row = {
        "totals": {"total": 28, "correct": 1, "abstained": 17, "incorrect": 10},
        "incorrect_rate": 10 / 28,
        "g_abs_recall": 1.0,
        "gates": {"g_abs": True, "g_err": False},
    }
    row.update(overrides)
    return row


def _l2_replacement(**overrides) -> dict:
    row = {
        "totals": {"total": 28, "correct": 26, "abstained": 2, "incorrect": 0},
        "incorrect_rate": 0.0,
        "g_abs_recall": 1.0,
        "cascade_skipped": True,
        "gates": {"g_abs": True, "g_err": True},
    }
    row.update(overrides)
    return row


def _write_report(tmp_path: Path, *, l1: dict, l2: dict, extra: dict | None = None) -> Path:
    payload = {"l1": l1, "l2_as_l1_replacement": l2, "cutover": True}
    if extra:
        payload.update(extra)
    path = tmp_path / "c7_cutover.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _clear_c7_env(monkeypatch):
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    monkeypatch.delenv("DMS_C7_RETIRE_CASCADE", raising=False)
    monkeypatch.delenv("DMS_C7_CUTOVER_REPORT", raising=False)
    yield


def test_default_flags_are_not_cutover():
    flags = cutover_flags()
    assert flags["l2_enabled"] is False
    assert flags["retire_cascade_requested"] is False
    assert flags["l2_replaces_l1"] is False
    assert flags["cascade_retired"] is False
    assert flags["cutover"] is False
    assert cascade_retired() is False


def test_retire_flag_alone_does_not_retire(monkeypatch):
    monkeypatch.setenv("DMS_C7_RETIRE_CASCADE", "1")
    flags = cutover_flags()
    assert flags["retire_cascade_requested"] is True
    assert flags["cascade_retired"] is False
    assert flags["cutover"] is False


def test_l2_enabled_alone_does_not_retire_cascade(monkeypatch):
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    flags = cutover_flags()
    assert flags["l2_enabled"] is True
    assert flags["cascade_retired"] is False
    assert flags["cutover"] is False


def test_json_cutover_true_is_ignored(tmp_path, monkeypatch):
    path = _write_report(tmp_path, l1=_l1_report(), l2=_l2_replacement())
    monkeypatch.setenv("DMS_C7_CUTOVER_REPORT", str(path))
    flags = cutover_flags()
    assert flags["l2_replaces_l1"] is True
    assert flags["cutover"] is False
    assert flags["cascade_retired"] is False


def test_beating_report_plus_flag_retires_cascade(tmp_path, monkeypatch):
    path = _write_report(tmp_path, l1=_l1_report(), l2=_l2_replacement())
    monkeypatch.setenv("DMS_C7_CUTOVER_REPORT", str(path))
    monkeypatch.setenv("DMS_C7_RETIRE_CASCADE", "1")
    flags = cutover_flags()
    assert flags["l2_replaces_l1"] is True
    assert flags["cascade_retired"] is True
    assert flags["cutover"] is False


def test_g_err_fail_does_not_replace_l1():
    l2 = _l2_replacement(
        totals={"total": 28, "correct": 10, "abstained": 10, "incorrect": 8},
        incorrect_rate=8 / 28,
        gates={"g_abs": True, "g_err": False},
    )
    assert l2_replaces_l1(_l1_report(), l2) is False


def test_g_abs_fail_does_not_replace_l1():
    l2 = _l2_replacement(
        g_abs_recall=0.5,
        gates={"g_abs": False, "g_err": True},
    )
    assert l2_replaces_l1(_l1_report(), l2) is False


def test_l2_on_miss_report_cannot_retire_cascade():
    l2 = _l2_replacement(cascade_skipped=False)
    assert l2_replaces_l1(_l1_report(), l2) is False
    l2.pop("cascade_skipped")
    assert l2_replaces_l1(_l1_report(), l2) is False


def test_l2_worse_than_l1_does_not_replace():
    l1 = _l1_report(
        totals={"total": 28, "correct": 26, "abstained": 2, "incorrect": 0},
        incorrect_rate=0.0,
        gates={"g_abs": True, "g_err": True},
    )
    l2 = _l2_replacement(
        totals={"total": 28, "correct": 20, "abstained": 7, "incorrect": 1},
        incorrect_rate=1 / 28,
        gates={"g_abs": True, "g_err": True},
    )
    assert l2_replaces_l1(l1, l2) is False


def test_missing_report_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_C7_RETIRE_CASCADE", "1")
    monkeypatch.setenv("DMS_C7_CUTOVER_REPORT", str(tmp_path / "missing.json"))
    assert cascade_retired() is False


def test_choose_governed_metric_uses_cascade_by_default():
    from CortexOS.dms.answer_engine import choose_governed_metric, route_to_metric

    plan = choose_governed_metric(L1_Q)
    assert plan is not None
    assert plan == route_to_metric(L1_Q)
    assert plan.metric_id == "suppliers_by_risk"


def test_choose_governed_metric_skips_cascade_when_retired(monkeypatch):
    from CortexOS.dms import answer_engine as ae

    monkeypatch.setattr(ae, "cascade_retired", lambda: True)
    assert ae.choose_governed_metric(L1_Q) is None
    assert ae.route_to_metric(L1_Q) is not None


def test_slot_helpers_remain_for_l0_and_skills():
    from CortexOS.dms.answer_engine import _days, _explicit_limit, _sales_rank_slots

    assert _days("last 14 days", 30) == 14
    assert _explicit_limit("top 3 skus") == 3
    slots = _sales_rank_slots("top 5 sku by revenue")
    assert slots.get("limit") == 5


@pytest.fixture(scope="module")
def ensure_db():
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.semantic.loader import reload

    _ensure_db_loaded()
    reload()
    yield


def test_default_serve_still_uses_l1_cascade(ensure_db):
    from CortexOS.dms.answer_engine import answer

    body = answer(L1_Q)
    assert body["layer"] == "governed_metric"
    assert body["badge"] == "governed_metric"
    rows = body.get("rows") or []
    assert rows
    assert "supplier" in (body.get("answer") or "").lower() or "risk" in (
        body.get("answer") or ""
    ).lower()


def test_retired_l1_miss_abstains_honestly(ensure_db, monkeypatch):
    from CortexOS.dms import answer_engine as ae
    from packs.dms.semantic import query_skills

    monkeypatch.setattr(ae, "cascade_retired", lambda: True)
    monkeypatch.setattr(query_skills, "find", lambda _q: None)
    body = ae.answer(L1_Q)
    assert body["layer"] != "governed_metric"
    assert body["badge"] in {"abstain", "needs_clarification"}
    assert body.get("rows") == []
    text = (body.get("answer") or "").lower()
    assert text.strip()
    assert "supplier" not in (body.get("sql_used") or "").lower()


def test_retired_l0_certified_still_serves(ensure_db, monkeypatch):
    from CortexOS.dms import answer_engine as ae

    monkeypatch.setattr(ae, "cascade_retired", lambda: True)
    body = ae.answer(L0_Q)
    assert body["layer"] == "certified"
    assert body["badge"] == "certified"
    assert body.get("rows")
    assert (body.get("answer") or "").strip()


def test_l2_on_does_not_skip_cascade_without_retirement(ensure_db, monkeypatch):
    from CortexOS.dms.answer_engine import answer

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    body = answer(L1_Q)
    assert body["layer"] == "governed_metric"
    assert body.get("rows")


def test_c7_05_score_engine_report_cannot_retire_cascade():
    """L2-on-miss (C7-05) reports have no cascade_skipped; they cannot retire L1."""
    from bench.heldout import HeldoutItem, score_engine

    item = HeldoutItem(
        id="must_abs",
        split="must_abstain",
        provenance="different_model_abstain",
        question="esg plus berlin weather 1997",
        expect="abstain",
    )

    def ask(question: str) -> dict:
        del question
        return {
            "answer": "I can't answer that.",
            "rows": [],
            "badge": "abstain",
            "route": "needs_clarification",
        }

    report = score_engine([item], ask=ask, count_shadow=False)
    assert report["cutover"] is False
    assert not report.get("cascade_skipped")
    assert l2_replaces_l1(report, report) is False
    assert cutover_flags({"l1": report, "l2_as_l1_replacement": report})[
        "cascade_retired"
    ] is False


def test_c7_cutover_does_not_import_packs() -> None:
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "CortexOS" / "dms" / "c7_cutover.py"
    tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    leaked = [m for m in mods if m == "packs" or m.startswith("packs.")]
    assert not leaked, f"c7_cutover.py must not import packs: {leaked}"
