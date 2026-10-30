"""Deterministic SQL guardrail — no LLM, parse + validate + cap + mask."""

from __future__ import annotations

from contextvars import ContextVar
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

# The pre-enforce statement a caller passed, and the total-order page actually
# fetched. ``ValidateGateResult.safe_sql`` reads this so a seat-locked caller
# still reports the tied SQL. The map is the request context, not a process global.
_SERVED_PAGE: ContextVar[dict[str, str] | None] = ContextVar("served_page_sql", default=None)


def remember_served_page(original: str, tied: str) -> None:
    if not original or original == tied:
        return
    current = dict(_SERVED_PAGE.get() or {})
    current[original] = tied
    _SERVED_PAGE.set(current)


def served_page_sql(sql: str | None) -> str | None:
    if sql is None:
        return None
    return (_SERVED_PAGE.get() or {}).get(sql, sql)


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


def catalog_outside_caller(table: exp.Table, own_catalog: str = "") -> bool:
    """True when a table names a catalog other than the caller's.

    A bare name and ``schema.table`` stay in the session catalog. A three-part
    name is another catalog, except the one catalog this caller already holds
    (empty means the caller named none, so every catalog qualifier is outside).
    """
    catalog = str(table.catalog or "").strip().lower()
    if not catalog:
        return False
    return catalog != own_catalog


#: Nodes a literal-only generator argument may contain: constants, the array,
#: struct and tuple constructors that hold them, a sign, and a typed literal
#: such as ``DATE '2024-01-01'``. No column, call, subquery or relation.
_LITERAL_ARGUMENT_NODES = (
    exp.Literal,
    exp.Null,
    exp.Boolean,
    exp.Neg,
    exp.Array,
    exp.Tuple,
    exp.Struct,
    exp.PropertyEQ,
    exp.Cast,
    exp.DataType,
)


def _literal_argument(node: exp.Expression) -> bool:
    for sub in node.walk():
        if isinstance(sub, _LITERAL_ARGUMENT_NODES):
            continue
        if isinstance(sub, exp.Identifier) and isinstance(sub.parent, exp.PropertyEQ):
            continue  # the key of a struct literal, {'a': 1}
        return False
    return True


def _relation_function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower() or "anonymous"
    if isinstance(node, exp.Func):
        return node.sql_name().lower()
    return type(node).__name__.lower()


def _unnest_is_literal_generator(node: exp.Unnest) -> bool:
    args = list(node.expressions)
    return bool(args) and all(_literal_argument(arg) for arg in args)


def relation_function_violations(stmt: exp.Expression) -> list[str]:
    """Name every function the statement reads as a relation.

    A function in FROM, a JOIN, a comma join or LATERAL is a relation the table
    and column checks below never see by name: ``read_csv``, ``glob``,
    ``duckdb_tables()`` or ``query_table`` all parse to a table with an empty
    name, and ``LATERAL read_csv(...)`` parses to no table at all. The walk
    covers subqueries and CTE bodies. The one function allowed is the plain
    generator this gate already served, ``UNNEST`` over literal arguments,
    which touches no storage. Anything else is refused by name.
    """
    found: list[str] = []
    for node in stmt.walk():
        if isinstance(node, exp.Table):
            if not isinstance(node.this, exp.Identifier):
                found.append(f"TABLE_FUNCTION:{_relation_function_name(node.this)}")
        elif isinstance(node, exp.Lateral):
            inner = node.this
            if isinstance(inner, exp.Subquery | exp.Select | exp.Unnest | exp.Table):
                continue  # a subquery is checked by its own nodes; the rest below
            found.append(f"TABLE_FUNCTION:{_relation_function_name(inner)}")
        elif isinstance(node, exp.Unnest):
            if isinstance(node.parent, exp.From | exp.Join | exp.Lateral) and not (
                _unnest_is_literal_generator(node)
            ):
                found.append("TABLE_FUNCTION:unnest")
        elif isinstance(node, exp.From | exp.Join):
            inner = node.this
            if isinstance(inner, exp.Func) and not isinstance(inner, exp.Unnest | exp.Lateral):
                found.append(f"TABLE_FUNCTION:{_relation_function_name(inner)}")
    return found


def _row_key(semantic: dict[str, Any], table: str) -> list[str]:
    spec = (semantic.get("tables") or {}).get(table) or {}
    cols = set(spec.get("columns") or [])
    return [str(name) for name in (spec.get("row_key") or []) if str(name) in cols]


def _under_window(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Window):
            return True
        parent = parent.parent
    return False


def _select_aggregate_without_group(stmt: exp.Select) -> bool:
    """A scalar aggregate is one row. It has no tie to break."""
    if stmt.args.get("group") is not None:
        return False
    for expr in stmt.expressions:
        for node in expr.walk():
            if isinstance(node, exp.AggFunc) and not _under_window(node):
                return True
    return False


def _order_refs(order: exp.Order) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    for ordered in order.expressions:
        expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
        if isinstance(expr, exp.Column):
            refs.add((expr.table or "", expr.name))
        else:
            refs.add(("", expr.sql(dialect="duckdb")))
    return refs


def _already_ordered(refs: set[tuple[str, str]], table: str, name: str) -> bool:
    """True when this column is already an ORDER BY key.

    An unqualified ``name`` covers every table. A qualified column covers only
    that qualifier: ``l.location_id`` does not order ``i.location_id``.
    """
    return (table, name) in refs or ("", name) in refs


def _cte_aliases(stmt: exp.Select) -> set[str]:
    with_ = stmt.args.get("with_") or stmt.args.get("with")
    if with_ is None:
        return set()
    names: set[str] = set()
    for cte in with_.expressions or []:
        alias = cte.alias
        if alias:
            names.add(str(alias))
    return names


def _outer_base_tables(stmt: exp.Select) -> list[exp.Table]:
    """Tables in the outer FROM / JOIN. Subqueries and CTE names are not base keys."""
    ctes = _cte_aliases(stmt)
    found: list[exp.Table] = []

    def take(node: exp.Expression | None) -> None:
        if isinstance(node, exp.Table) and node.name and node.name not in ctes:
            found.append(node)

    from_ = stmt.args.get("from_") or stmt.args.get("from")
    if from_ is not None:
        take(from_.this)
    for join in stmt.args.get("joins") or []:
        take(join.this)
    return found


def _ordered(expr: exp.Expression) -> exp.Ordered:
    return exp.Ordered(this=expr, desc=False)


def _append_missing(order: exp.Order, refs: set[tuple[str, str]], columns: list[exp.Column]) -> None:
    for col in columns:
        table = col.table or ""
        name = col.name
        if not name or _already_ordered(refs, table, name):
            continue
        order.append("expressions", _ordered(col))
        refs.add((table, name))


def _group_key_columns(stmt: exp.Select) -> list[exp.Column]:
    group = stmt.args.get("group")
    cols: list[exp.Column] = []
    for expr in group.expressions or []:
        if isinstance(expr, exp.Column):
            cols.append(expr.copy())
    return cols


def _distinct_columns(stmt: exp.Select) -> list[exp.Column]:
    """DISTINCT output is totally ordered by the selected columns, not a hidden key."""
    cols: list[exp.Column] = []
    for expr in stmt.expressions:
        if isinstance(expr, exp.Alias):
            cols.append(exp.column(expr.alias))
        elif isinstance(expr, exp.Column):
            cols.append(expr.copy())
    return cols


def _row_key_columns(stmt: exp.Select, semantic: dict[str, Any]) -> list[exp.Column]:
    cols: list[exp.Column] = []
    for table in _outer_base_tables(stmt):
        qual = table.alias or table.name
        for name in _row_key(semantic, table.name):
            cols.append(exp.column(name, table=qual))
    return cols


def _append_total_order(stmt: exp.Select, semantic: dict[str, Any]) -> None:
    """Make an ordered page a total order.

    GROUP BY ties break on the group key (a base-table column there is a
    binder error). DISTINCT ties break on the selected columns. A plain
    page ties on each outer table's ``row_key``. No ORDER BY stays unordered.
    """
    order = stmt.args.get("order")
    if order is None or stmt.args.get("limit") is None:
        return
    if _select_aggregate_without_group(stmt):
        return
    refs = _order_refs(order)
    if stmt.args.get("group") is not None:
        columns = _group_key_columns(stmt)
    elif stmt.args.get("distinct"):
        columns = _distinct_columns(stmt)
    else:
        columns = _row_key_columns(stmt, semantic)
    _append_missing(order, refs, columns)


def _limit_value(stmt: exp.Select) -> int | None:
    limit = stmt.args.get("limit")
    if limit is None:
        return None
    try:
        return int(limit.expression.this)
    except (TypeError, ValueError, AttributeError):
        return None


def _unlimited(sql: str) -> str:
    """The statement with ORDER BY and LIMIT removed, for a page count."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    tree.set("limit", None)
    tree.set("order", None)
    return tree.sql(dialect="duckdb")


def apply_page_tiebreak(sql: str, semantic: dict[str, Any], total_count: int | None) -> str:
    """Total-order a page that the LIMIT can cut. Leave every other statement byte-identical.

    DuckDB does not stable-sort. When ``total_count`` is above the LIMIT, two
    engine versions can return different rows for one ORDER BY. Append the
    driving table's key (or the GROUP BY grain) so that page is a prefix of
    one total order. Metric SQL and generated SQL both call this; there is no
    per-question branch. A result that already fits in the LIMIT cannot drop a
    tied row, so its SQL is untouched. An unknown count fails closed and
    applies the key. A pack with no ``row_key`` is left alone.
    """
    try:
        stmt = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return sql
    if not isinstance(stmt, exp.Select):
        return sql
    if stmt.args.get("order") is None or stmt.args.get("limit") is None:
        return sql
    limit = _limit_value(stmt)
    if total_count is not None and limit is not None and total_count <= limit:
        return sql
    order = stmt.args["order"]
    before = len(order.expressions)
    _append_total_order(stmt, semantic)
    if len(order.expressions) == before:
        return sql
    return stmt.sql(dialect="duckdb")


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

    # Column without a relation is the fenced-SELECT-list bug (`SELECT i.sku`).
    # Session follow-ups emit `SELECT CAST(n AS DOUBLE) AS sum_…` with no FROM;
    # that is a literal, not an unbound alias.
    has_table = any(True for _ in stmt.find_all(exp.Table))
    has_column = any(True for _ in stmt.find_all(exp.Column))
    if has_column and not has_table:
        violations.append("MISSING_FROM")
        return GuardrailResult(False, violations, None)

    for node in stmt.walk():
        if isinstance(node, FORBIDDEN):
            violations.append("DDL_ATTEMPT")
            return GuardrailResult(False, violations, None)

    table_functions = relation_function_violations(stmt)
    if table_functions:
        # Fail closed before any name check: a function relation has no name
        # the grant can be matched against.
        violations.extend(dict.fromkeys(table_functions))
        return GuardrailResult(False, violations, None)

    tables = _allowed_tables(semantic)
    sensitive = _sensitive_columns(semantic)
    own_catalog = str(semantic.get("catalog") or "").strip().lower()

    referenced_tables: set[str] = set()
    referenced_columns: set[str] = set()
    all_cols = _all_allowed_columns(semantic)

    for table in stmt.find_all(exp.Table):
        if catalog_outside_caller(table, own_catalog):
            # The name of the gate, not the SQL. The statement is not safe_sql.
            violations.append("cross-catalog table reference refused")
            return GuardrailResult(False, violations, None)
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

    # #402: the same declared-value check execute_sql applies. On a lake that
    # does not store the declared values, a filter on them matches nothing.
    # A stale lake is a refusal: do not count it and do not fetch it.
    from CortexOS.dms.warehouse_db import connection_instance, serving_value_domains
    from CortexOS.ontology.value_domain import stale_columns, stale_reason

    drift = stale_columns(
        con, result.safe_sql, serving_value_domains(), instance=connection_instance(con)
    )
    if drift:
        stale = GuardrailResult(passed=False, violations=[stale_reason(drift)], safe_sql=None)
        entry.safe_sql = None
        entry.violations = [item.code for item in drift]
        entry.passed = False
        log_audit(entry)
        return stale, [], entry

    # The statement is about to run. Count first so a result that already
    # fits in the LIMIT keeps its SQL bytes.
    try:
        counted = con.execute(
            "SELECT COUNT(*) FROM (" + _unlimited(result.safe_sql) + ") _page"
        ).fetchone()
        total = int(counted[0]) if counted else None
    except Exception:  # noqa: BLE001
        total = None
    tied = apply_page_tiebreak(result.safe_sql, semantic, total)
    if tied != result.safe_sql:
        result = GuardrailResult(True, [], tied)
        entry.safe_sql = tied

    rel = con.execute(result.safe_sql)
    rows_raw = rel.fetchall()
    columns = [d[0] for d in rel.description] if rel.description else []
    rows = [dict(zip(columns, row, strict=False)) for row in rows_raw]
    entry.row_count = len(rows)
    log_audit(entry)
    return result, rows, entry
