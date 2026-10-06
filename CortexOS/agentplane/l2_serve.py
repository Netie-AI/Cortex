"""Serve Crew Insights L2 SQL only after the governed numeric gates."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from CortexOS.dms.l2_plan_gates import (
    L2ServePlan,
    plan_shape_violation,
    prepare_listing_plan,
    prepare_plan,
)


def enabled() -> bool:
    """The cutover is explicit and remains off by default."""
    return os.environ.get("DMS_L2_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def _step_abstain(step: str, reason: str, *, model_called: bool) -> dict[str, Any]:
    called = str(model_called).lower()
    return {
        "ok": True,
        "status": "ABSTAIN",
        "phase": "l2_serve",
        "layer": "abstain",
        "badge": "abstain",
        "answer_step": step,
        "answer": f"{step} abstained: {reason}; model_called={called}",
        "audit_id": None,
        "values": [],
        "rows": [],
        "row_count": 0,
        "sql_used": None,
        "model_called": model_called,
    }


def _render_rows(rows: list[dict[str, Any]]) -> str:
    return "; ".join(
        ", ".join(f"{key}={value}" for key, value in row.items()) for row in rows
    )


def serve_on_miss(
    *,
    intent: str,
    generated: Mapping[str, Any] | None,
    ranking: Mapping[str, Any],
    bridge: Any,
    query_plan: Mapping[str, Any] | None,
    pack_dir: Path,
) -> dict[str, Any] | None:
    """Return ``None`` when disabled; otherwise serve or name the failed gate."""
    if not enabled():
        return None

    stamp = (generated or {}).get("stamp")
    model_called = bool(generated)
    stamped_call = isinstance(stamp, Mapping) and bool(
        str(stamp.get("call_id") or "").strip()
    )
    sql = str((generated or {}).get("sql") or "").strip()
    if not (generated or {}).get("ok") or not sql:
        return _step_abstain(
            "l2_generation",
            str((generated or {}).get("refuse_reason") or "no SQL"),
            model_called=model_called,
        )
    if not stamped_call:
        return _step_abstain(
            "route_stamp",
            "generated SQL has no RouteStamp call_id",
            model_called=model_called,
        )

    plan, plan_reason = prepare_plan(
        ranking, query_plan, sql, pack_dir
    )
    if plan is None:
        return _step_abstain("l2_plan", plan_reason, model_called=model_called)
    executable = plan.sql

    try:
        from CortexOS.execution.session_manifests import get_session_registry

        verified = get_session_registry().resolve(
            getattr(bridge, "session_id", None),
            space_id=getattr(bridge, "space_id", None),
        )
    except Exception as exc:  # noqa: BLE001 - all missing/expired grants abstain by step
        return _step_abstain(
            "manifest_check", f"{type(exc).__name__}: {exc}", model_called=model_called
        )

    try:
        from CortexOS.dms.sql_validate_gate import SqlGateAbstain
        from CortexOS.execution.manifest import ManifestError
        from CortexOS.execution.submit import execute_sql

        # execute_sql is the ordered security seam: enforce_manifest -> EXPLAIN -> fetch.
        rows, _, _ = execute_sql(verified, executable, explain_gate=True)
    except ManifestError as exc:
        return _step_abstain(
            "manifest_check", f"{type(exc).__name__}: {exc}", model_called=model_called
        )
    except SqlGateAbstain as exc:
        return _step_abstain(
            "explain", f"{type(exc).__name__}: {exc}", model_called=model_called
        )
    except Exception as exc:  # noqa: BLE001 - execution errors are named abstentions
        return _step_abstain(
            "execute", f"{type(exc).__name__}: {exc}", model_called=model_called
        )

    from CortexOS.dms.l2_plausibility import assess_plausibility, sql_table_names

    retrieved = sorted(
        {
            str((row.get("where") or {}).get("table") or row.get("id") or "").lower()
            for row in ranking.get("locations") or []
            if isinstance(row, dict)
        }
    )
    try:
        plausible = assess_plausibility(
            intent, executable, rows, retrieved_tables=[item for item in retrieved if item]
        )
    except Exception as exc:  # noqa: BLE001 - a broken gate must fail closed
        return _step_abstain(
            "plausibility", f"{type(exc).__name__}: {exc}", model_called=model_called
        )
    if not plausible.ok:
        return _step_abstain(
            "plausibility", plausible.reason or plausible.code, model_called=model_called
        )

    detail = plan_shape_violation(plan, rows)
    if detail is not None:
        return _step_abstain("plan_shape", detail, model_called=model_called)

    audit_id = str(uuid.uuid4())
    sources = sorted(sql_table_names(executable))
    answer = (
        "l2_plausibility served after manifest_check and explain "
        f"(model_called={str(model_called).lower()}): {_render_rows(rows)}"
    )
    return {
        "ok": True,
        "status": "CERTIFIED",
        "phase": "l2_serve",
        "layer": "generated",
        "badge": "L2_VALIDATED",
        "answer_step": "l2_plausibility",
        "answer": answer,
        "audit_id": audit_id,
        "values": rows,
        "rows": rows,
        "row_count": len(rows),
        "sql_used": executable,
        "sources": sources,
        "truncated": False,
        "model_called": model_called,
    }


__all__ = [
    "L2ServePlan",
    "enabled",
    "plan_shape_violation",
    "prepare_listing_plan",
    "prepare_plan",
    "serve_on_miss",
]
