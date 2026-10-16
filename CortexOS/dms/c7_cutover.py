"""C7-06 — ``route_to_metric`` is not the serve chooser once L2 beats L1.

The 25 ``_metric_plan`` branches stay until a commit body carries held-out
numbers. A flag or a JSON ``cutover: true`` cannot invent that. Keyword slot
helpers remain on ``answer_engine`` for L0 / query-skill slots.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

G_ERR_MAX = 0.02


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def l2_enabled() -> bool:
    return _env_on("DMS_L2_ENABLED")


def retire_cascade_requested() -> bool:
    return _env_on("DMS_C7_RETIRE_CASCADE")


def load_cutover_report(path: str | None = None) -> dict[str, Any] | None:
    raw = path if path is not None else os.environ.get("DMS_C7_CUTOVER_REPORT", "")
    raw = (raw or "").strip()
    if not raw:
        return None
    target = Path(raw)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _totals(report: dict[str, Any]) -> dict[str, Any]:
    totals = report.get("totals")
    return totals if isinstance(totals, dict) else {}


def incorrect_count(report: dict[str, Any]) -> int:
    try:
        return int(_totals(report).get("incorrect") or 0)
    except (TypeError, ValueError):
        return 0


def incorrect_rate(report: dict[str, Any]) -> float:
    raw = report.get("incorrect_rate")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 1.0
    totals = _totals(report)
    try:
        total = int(totals.get("total") or 0)
        bad = int(totals.get("incorrect") or 0)
    except (TypeError, ValueError):
        return 1.0
    return (bad / total) if total else 1.0


def g_abs_holds(report: dict[str, Any]) -> bool:
    gates = report.get("gates")
    if isinstance(gates, dict) and "g_abs" in gates:
        return bool(gates["g_abs"])
    recall = report.get("g_abs_recall")
    if recall is None:
        return False
    try:
        return float(recall) >= 1.0
    except (TypeError, ValueError):
        return False


def g_err_holds(report: dict[str, Any]) -> bool:
    gates = report.get("gates")
    if isinstance(gates, dict) and "g_err" in gates:
        if not gates["g_err"]:
            return False
        return incorrect_rate(report) < G_ERR_MAX
    return incorrect_rate(report) < G_ERR_MAX


def l2_replaces_l1(
    l1_report: dict[str, Any] | None,
    l2_report: dict[str, Any] | None,
) -> bool:
    """True only when L2-as-L1-replacement beats L1 on G-err and G-abs holds.

    G-err: L2 incorrect < 2% and <= L1 incorrect on the same items. L2 must
    have run with the cascade skipped; an L2-on-miss (C7-05) report cannot
    retire the chooser.
    """
    if not isinstance(l1_report, dict) or not isinstance(l2_report, dict):
        return False
    if not l2_report.get("cascade_skipped"):
        return False
    if not g_abs_holds(l1_report) or not g_abs_holds(l2_report):
        return False
    if not g_err_holds(l2_report):
        return False
    return incorrect_count(l2_report) <= incorrect_count(l1_report)


def cutover_flags(report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Operator-visible flags. ``cutover`` is never inferred from a file."""
    data = report if report is not None else load_cutover_report()
    l1 = l2 = None
    if isinstance(data, dict):
        l1 = data.get("l1")
        l2 = data.get("l2_as_l1_replacement")
    replaces = l2_replaces_l1(
        l1 if isinstance(l1, dict) else None,
        l2 if isinstance(l2, dict) else None,
    )
    requested = retire_cascade_requested()
    return {
        "l2_enabled": l2_enabled(),
        "retire_cascade_requested": requested,
        "l2_replaces_l1": replaces,
        "cascade_retired": bool(requested and replaces),
        "cutover": False,
    }


def cascade_retired() -> bool:
    return bool(cutover_flags()["cascade_retired"])
