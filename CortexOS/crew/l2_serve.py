"""Serve Crew Insights L2 SQL only after the governed numeric gates."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from sqlglot import exp


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


def _name(node: exp.Expression) -> str:
    name = node.alias_or_name
    return str(name or "").strip().lower()


def _shape(sql: str) -> tuple[set[str], set[str]] | None:
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001 - a parse miss is a named abstain
        return None
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None or any(isinstance(item, exp.Star) for item in select.expressions):
        return None
    outputs = {_name(item) for item in select.expressions}
    if "" in outputs or len(outputs) != len(select.expressions):
        return None
    group = select.args.get("group")
    grains = {_name(item) for item in (group.expressions if group is not None else [])}
    if "" in grains:
        return None
    return outputs, grains


def _canonical(sql: str) -> str | None:
    try:
        return sqlglot.parse_one(sql, read="duckdb").sql(
            dialect="duckdb", normalize=True, pretty=False
        )
    except Exception:  # noqa: BLE001
        return None


def _matches_stored(generated_sql: str, stored_sql: str) -> bool:
    """Accept only stored SQL, plus the validator's deterministic safety cap."""
    generated = _canonical(generated_sql)
    stored = _canonical(stored_sql)
    return bool(
        generated
        and stored
        and (generated == stored or generated == f"{stored} LIMIT 1000")
    )


def _docs(pack_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    semantic = pack_dir / "semantic"
    metrics = yaml.safe_load((semantic / "metrics.yaml").read_text(encoding="utf-8")) or {}
    certified = (
        yaml.safe_load((semantic / "certified_queries.yaml").read_text(encoding="utf-8"))
        or {}
    )
    return metrics, certified


def _stored_plan(
    ranking: Mapping[str, Any],
    query_plan: Mapping[str, Any] | None,
    generated_sql: str,
    pack_dir: Path,
) -> tuple[str, set[str], set[str], str] | tuple[None, None, None, str]:
    """Return executable SQL and its deterministic output/grain oracle."""
    try:
        metrics_doc, certified_doc = _docs(pack_dir)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return None, None, None, f"plan unreadable: {type(exc).__name__}"

    metrics_by_id = {
        str(row.get("id") or ""): row
        for row in metrics_doc.get("metrics") or []
        if isinstance(row, dict)
    }
    measure = str((query_plan or {}).get("measure") or "").strip()
    metric = metrics_by_id.get(measure)
    measure_columns = {
        str(item).strip().lower()
        for item in (metric or {}).get("result_columns") or []
        if str(item).strip()
    }
    certified_by_id = {
        str(row.get("id") or ""): row
        for row in certified_doc.get("certified") or []
        if isinstance(row, dict)
    }
    ranked_certified_refs = [
        row for row in ranking.get("certified") or [] if isinstance(row, dict)
    ]
    ranked_certified_refs.sort(
        key=lambda row: -int((row.get("importance") or {}).get("score") or 0)
    )
    ranked_certified = [
        certified_by_id.get(str(row.get("id") or "")) for row in ranked_certified_refs
    ]
    ranked_certified = [row for row in ranked_certified if row]
    exact = next(
        (
            row
            for row in ranked_certified
            if _matches_stored(generated_sql, str(row.get("sql") or ""))
        ),
        None,
    )
    planned = [
        row
        for row in ranked_certified
        if (
            (shape := _shape(str(row.get("sql") or ""))) is not None
            and measure_columns
            and measure_columns <= shape[0]
        )
    ]
    if exact is not None or planned:
        selected = exact or planned[0]
        stored_sql = str(selected.get("sql") or "").strip()
        if exact is None or not stored_sql:
            return None, None, None, (
                f"certified_measure_not_used:{selected.get('id')}"
            )
        shape = _shape(stored_sql)
        if shape is None:
            return None, None, None, "stored certified query has no provable shape"
        return stored_sql, shape[0], shape[1], f"certified:{selected.get('id')}"

    ranked_ids = [
        str(row.get("id") or "")
        for row in ranking.get("metrics") or []
        if isinstance(row, dict)
    ]
    if metric is None:
        metric = next((metrics_by_id[mid] for mid in ranked_ids if mid in metrics_by_id), None)
    if metric is None:
        return None, None, None, "no certified metric plan matched the generated SQL"

    result_columns = {
        str(item).strip().lower()
        for item in metric.get("result_columns") or []
        if str(item).strip()
    }
    if not result_columns:
        return None, None, None, f"metric {metric.get('id')} has no result_columns"
    stored_shape = _shape(str(metric.get("sql") or ""))
    stored_grain = stored_shape[1] if stored_shape is not None else set()
    if query_plan is not None:
        raw_group = query_plan.get("group_by") or []
        if not isinstance(raw_group, Sequence) or isinstance(raw_group, (str, bytes)):
            return None, None, None, "query plan group_by is not a list"
        expected_grain = {str(item).strip().lower() for item in raw_group if str(item).strip()}
        measure_columns = result_columns - stored_grain
        expected_columns = expected_grain | measure_columns
    else:
        expected_grain = stored_grain
        expected_columns = result_columns
    return generated_sql, expected_columns, expected_grain, f"metric:{metric.get('id')}"


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

    executable, expected_columns, expected_grain, plan_name = _stored_plan(
        ranking, query_plan, sql, pack_dir
    )
    if executable is None or expected_columns is None or expected_grain is None:
        return _step_abstain("l2_plan", plan_name, model_called=model_called)

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

    actual = _shape(executable)
    row_columns = {str(key).strip().lower() for row in rows for key in row}
    if (
        actual is None
        or actual[0] != expected_columns
        or actual[1] != expected_grain
        or (rows and row_columns != expected_columns)
    ):
        detail = (
            f"{plan_name} expected columns={sorted(expected_columns)} "
            f"grain={sorted(expected_grain)}; "
            f"got columns={sorted(actual[0]) if actual else []} "
            f"grain={sorted(actual[1]) if actual else []} row_columns={sorted(row_columns)}"
        )
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


__all__ = ["enabled", "serve_on_miss"]
