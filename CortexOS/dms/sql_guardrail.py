"""Deterministic SQL guardrail — no LLM, parse + validate + cap + mask."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import sqlglot
from sqlglot import exp

FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
)
MAX_LIMIT = 1000


@dataclass(slots=True)
class GuardrailResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    safe_sql: str | None = None


def _allowed_tables(semantic: dict[str, Any]) -> set[str]:
    tables = semantic.get("tables") or {}
    if tables:
        return set(tables.keys())
    return {"inventory"}


def _all_allowed_columns(semantic: dict[str, Any]) -> set[str]:
    tables = semantic.get("tables") or {}
    cols: set[str] = set()
    for spec in tables.values():
        cols.update(spec.get("columns") or [])
    if not cols:
        cols = {
            "sku",
            "sku_name",
            "quantity_kg",
            "last_restocked",
            "location_id",
            "reorder_level_kg",
            "email",
            "phone",
            "contact_person",
        }
    return cols


def _allowed_columns(semantic: dict[str, Any], table: str) -> set[str]:
    tables = semantic.get("tables") or {}
    if table in tables:
        return set(tables[table].get("columns") or [])
    return _all_allowed_columns(semantic)


def _sensitive_columns(semantic: dict[str, Any]) -> set[str]:
    return set(semantic.get("sensitive_columns") or [])


def validate_sql(sql: str, semantic: dict[str, Any]) -> GuardrailResult:
    violations: list[str] = []
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as exc:
        return GuardrailResult(False, [f"PARSE_ERROR:{exc}"], None)

    if not statements or statements[0] is None:
        return GuardrailResult(False, ["PARSE_ERROR:empty"], None)

    if len(statements) > 1:
        violations.append("MULTI_STATEMENT")

    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        violations.append("DDL_ATTEMPT")
        return GuardrailResult(False, violations, None)

    if not any(True for _ in stmt.find_all(exp.Table)):
        violations.append("MISSING_FROM")
        return GuardrailResult(False, violations, None)

    for node in stmt.walk():
        if isinstance(node, FORBIDDEN):
            violations.append("DDL_ATTEMPT")
            return GuardrailResult(False, violations, None)

    tables = _allowed_tables(semantic)
    sensitive = _sensitive_columns(semantic)

    referenced_tables: set[str] = set()
    referenced_columns: set[str] = set()
    all_cols = _all_allowed_columns(semantic)

    for table in stmt.find_all(exp.Table):
        name = table.name
        referenced_tables.add(name)
        if name not in tables:
            violations.append(f"UNKNOWN_TABLE:{name}")

    select_aliases: set[str] = set()
    for expr in stmt.expressions:
        alias = getattr(expr, "alias", None)
        if alias:
            select_aliases.add(str(alias))

    for col in stmt.find_all(exp.Column):
        if col.name:
            referenced_columns.add(col.name)
            if col.name not in all_cols and col.name != "*" and col.name not in select_aliases:
                violations.append(f"UNKNOWN_COLUMN:{col.name}")

    selected_star = any(
        isinstance(expr, exp.Star)
        or (isinstance(expr, exp.Column) and expr.name == "*")
        for expr in stmt.expressions
    )
    if selected_star and sensitive:
        inv_cols = [c for c in _allowed_columns(semantic, "inventory") if c not in sensitive]
        stmt.set("expressions", [exp.column(c) for c in inv_cols])

    if violations:
        return GuardrailResult(False, violations, None)

    # Enforce LIMIT
    limit_node = stmt.args.get("limit")
    if limit_node is None:
        stmt.set("limit", exp.Limit(expression=exp.Literal.number(MAX_LIMIT)))
    else:
        try:
            val = int(limit_node.expression.this)
            if val > MAX_LIMIT:
                stmt.set("limit", exp.Limit(expression=exp.Literal.number(MAX_LIMIT)))
        except (TypeError, ValueError, AttributeError):
            stmt.set("limit", exp.Limit(expression=exp.Literal.number(MAX_LIMIT)))

    safe = stmt.sql(dialect="duckdb")

    return GuardrailResult(True, [], safe)


@dataclass(slots=True)
class AuditEntry:
    timestamp: str
    original_sql: str
    safe_sql: str | None
    violations: list[str]
    passed: bool
    row_count: int | None = None


_AUDIT_LOG: list[AuditEntry] = []


def audit_log() -> list[AuditEntry]:
    return list(_AUDIT_LOG)


def clear_audit_log() -> None:
    _AUDIT_LOG.clear()


def log_audit(entry: AuditEntry) -> None:
    _AUDIT_LOG.append(entry)


def guard_and_execute(sql: str, semantic: dict[str, Any], con) -> tuple[GuardrailResult, list[dict], AuditEntry]:
    result = validate_sql(sql, semantic)
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        original_sql=sql,
        safe_sql=result.safe_sql,
        violations=result.violations,
        passed=result.passed,
    )
    if not result.passed or not result.safe_sql:
        log_audit(entry)
        return result, [], entry

    rel = con.execute(result.safe_sql)
    rows_raw = rel.fetchall()
    columns = [d[0] for d in rel.description] if rel.description else []
    rows = [dict(zip(columns, row, strict=False)) for row in rows_raw]
    entry.row_count = len(rows)
    log_audit(entry)
    return result, rows, entry
