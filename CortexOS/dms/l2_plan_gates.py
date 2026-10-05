"""Pure stored-plan, schema-listing, column, and grain gates for L2 serving."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from sqlglot import exp


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


def ask_ranking(
    generated_sql: str,
    retrieved_tables: Sequence[str],
    pack_dir: Path,
) -> dict[str, Any]:
    """Derive ask-route gate context from exact stored SQL and retrieved schema."""
    try:
        metrics_doc, certified_doc = _docs(pack_dir)
        layer = yaml.safe_load(
            (pack_dir / "semantic_layer.yaml").read_text(encoding="utf-8")
        ) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return {"locations": [], "metrics": [], "certified": []}

    metrics = [
        {"id": str(row.get("id") or ""), "importance": {"score": 1}}
        for row in metrics_doc.get("metrics") or []
        if isinstance(row, dict)
        and _matches_stored(generated_sql, str(row.get("sql") or ""))
    ]
    certified = [
        {"id": str(row.get("id") or ""), "importance": {"score": 2}}
        for row in certified_doc.get("certified") or []
        if isinstance(row, dict)
        and _matches_stored(generated_sql, str(row.get("sql") or ""))
    ]
    tables = layer.get("tables") or {}
    locations = []
    for raw_table in retrieved_tables:
        table = str(raw_table or "").strip()
        spec = tables.get(table)
        if not table or not isinstance(spec, dict):
            continue
        locations.append(
            {
                "id": table,
                "where": {
                    "table": table,
                    "columns": [
                        str(column)
                        for column in spec.get("columns") or []
                        if str(column).strip()
                    ],
                },
            }
        )
    return {"locations": locations, "metrics": metrics, "certified": certified}


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


@dataclass(frozen=True, slots=True)
class L2ServePlan:
    """Stored numeric plan that generated SQL must preserve."""

    sql: str
    expected_columns: frozenset[str]
    expected_grain: frozenset[str]
    name: str


def prepare_plan(
    ranking: Mapping[str, Any],
    query_plan: Mapping[str, Any] | None,
    generated_sql: str,
    pack_dir: Path,
) -> tuple[L2ServePlan | None, str]:
    """Resolve the shared certified-measure, column, and grain oracle."""
    executable, columns, grain, name = _stored_plan(
        ranking, query_plan, generated_sql, pack_dir
    )
    if executable is None or columns is None or grain is None:
        return None, name
    return (
        L2ServePlan(
            sql=executable,
            expected_columns=frozenset(columns),
            expected_grain=frozenset(grain),
            name=name,
        ),
        "",
    )


def prepare_listing_plan(
    ranking: Mapping[str, Any],
    generated_sql: str,
) -> tuple[L2ServePlan | None, str]:
    """Build a schema-grounded oracle for a non-numeric listing."""
    try:
        tree = sqlglot.parse_one(generated_sql, read="duckdb")
    except Exception:  # noqa: BLE001
        return None, "generated listing is not parseable"
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None or any(item.find(exp.AggFunc) for item in select.expressions):
        return None, "generated SQL is not a schema listing"
    shape = _shape(generated_sql)
    if shape is None:
        return None, "generated listing has no provable shape"

    catalog = {
        str((row.get("where") or {}).get("table") or "").strip().lower(): {
            str(column).strip().lower()
            for column in (row.get("where") or {}).get("columns") or []
            if str(column).strip()
        }
        for row in ranking.get("locations") or []
        if isinstance(row, Mapping)
    }
    tables = {
        str(table.name or "").strip().lower()
        for table in tree.find_all(exp.Table)
        if str(table.name or "").strip()
    }
    columns = {
        str(column.name or "").strip().lower()
        for column in select.find_all(exp.Column)
        if str(column.name or "").strip()
    }
    allowed_columns = set().union(*(catalog.get(table, set()) for table in tables))
    if not tables or not tables <= catalog.keys():
        return None, "retrieval miss: generated listing reads outside ranked tables"
    if not columns or not columns <= allowed_columns:
        return None, "generated listing uses columns outside the ranked schema"
    return (
        L2ServePlan(
            sql=generated_sql,
            expected_columns=frozenset(shape[0]),
            expected_grain=frozenset(shape[1]),
            name=f"schema_listing:{','.join(sorted(tables))}",
        ),
        "",
    )


def plan_shape_violation(
    plan: L2ServePlan,
    rows: Sequence[Mapping[str, Any]],
) -> str | None:
    """Name a generated result that changes the stored columns or grain."""
    actual = _shape(plan.sql)
    row_columns = {
        str(key).strip().lower()
        for row in rows
        for key in row
    }
    if (
        actual is not None
        and actual[0] == plan.expected_columns
        and actual[1] == plan.expected_grain
        and (not rows or row_columns == plan.expected_columns)
    ):
        return None
    return (
        f"{plan.name} expected columns={sorted(plan.expected_columns)} "
        f"grain={sorted(plan.expected_grain)}; "
        f"got columns={sorted(actual[0]) if actual else []} "
        f"grain={sorted(actual[1]) if actual else []} "
        f"row_columns={sorted(row_columns)}"
    )


__all__ = [
    "L2ServePlan",
    "ask_ranking",
    "plan_shape_violation",
    "prepare_listing_plan",
    "prepare_plan",
]
