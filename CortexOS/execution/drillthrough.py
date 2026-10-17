"""T7 drill-through — rewrite answer SQL to contributing rows + provenance.

Architecture §4.5–4.6: strip aggregates, keep joins/WHERE, project provenance
columns, LIMIT 5000. Execution must use the same signed session manifest as
the original answer (HMAC token binds session + sql digest).
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

# Flat T7 (DMS) + optional STRUCT Appendix A.
_PROVENANCE_COLS = ("_src_ref_id", "_src_row", "_ingest_id", "_src")
_DRILL_LIMIT = 5000
_TOKEN_TTL_S = 3600
_SOME_GROUPS = "drill-through cannot derive rows for an answer that keeps only some groups"


class DrillthroughError(Exception):
    code = "drillthrough_error"


class DrillthroughTokenInvalid(DrillthroughError):
    code = "drillthrough_token_invalid"


class DrillthroughSessionMismatch(DrillthroughError):
    code = "drillthrough_session_mismatch"


class DrillthroughRefused(DrillthroughError):
    """The manifest or the read pool refused the drill SQL; keeps that refusal's code."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


#: (schema or None, table) -> provenance columns that table actually has.
ProvenanceLookup = Callable[[str | None, str], Sequence[str]]


@dataclass(slots=True)
class DrillRewrite:
    sql: str
    count_sql: str
    approximate: bool
    measure_expr: str | None
    #: Counts the answer's groups when its LIMIT may have dropped some.
    group_count_sql: str | None = None
    #: The answer's LIMIT; more groups than this means the drill cannot be exact.
    group_limit: int | None = None
    #: Counts the answer's own rows when its QUALIFY may have dropped groups;
    #: fewer rows than ``group_count_sql`` groups means the drill cannot be exact.
    kept_count_sql: str | None = None


def _hmac_key() -> bytes:
    raw = os.environ.get("CORTEX_DRILLTHROUGH_HMAC") or "cortex-drillthrough-dev-only"
    return raw.encode("utf-8")


def _b64url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    import base64

    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def sql_digest(sql: str) -> str:
    return hashlib.sha256(sql.strip().encode("utf-8")).hexdigest()


def mint_token(
    *,
    answer_id: str,
    session_id: str,
    manifest_hash: str,
    sql: str,
    expires_at: float | None = None,
) -> str:
    """HMAC token binding answer + session + manifest + sql digest."""
    payload = {
        "answer_id": answer_id,
        "session_id": session_id,
        "manifest_hash": manifest_hash,
        "sql_digest": sql_digest(sql),
        "sql": sql,
        "expires_at": expires_at if expires_at is not None else time.time() + _TOKEN_TTL_S,
    }
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url(hmac.new(_hmac_key(), body.encode("ascii"), hashlib.sha256).digest())
    return f"dt_{body}.{sig}"


def verify_token(token: str) -> dict[str, Any]:
    if not token or not token.startswith("dt_"):
        raise DrillthroughTokenInvalid("token must start with dt_")
    try:
        body, sig = token[3:].rsplit(".", 1)
    except ValueError as exc:
        raise DrillthroughTokenInvalid("malformed token") from exc
    expect = _b64url(hmac.new(_hmac_key(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expect, sig):
        raise DrillthroughTokenInvalid("token signature mismatch")
    try:
        payload = json.loads(_b64url_decode(body))
    except Exception as exc:  # noqa: BLE001
        raise DrillthroughTokenInvalid("token payload corrupt") from exc
    if float(payload.get("expires_at") or 0) < time.time():
        raise DrillthroughTokenInvalid("token expired")
    for key in ("answer_id", "session_id", "manifest_hash", "sql_digest", "sql"):
        if key not in payload:
            raise DrillthroughTokenInvalid(f"token missing {key}")
    return payload


def _top_level_aggregates(node: exp.Expression) -> list[exp.Expression]:
    """Function calls in ``node`` outside any subquery or window."""
    return [
        f
        for f in node.find_all(exp.AggFunc, exp.Anonymous)
        if f.find_ancestor(exp.Select, exp.Window) is None
    ]


@functools.lru_cache(maxsize=1)
def _engine_function_kinds() -> dict[str, str]:
    """DuckDB function name -> ``aggregate`` / ``scalar`` / ``unknown``.

    sqlglot parses some DuckDB aggregates (MEAN, PRODUCT, HISTOGRAM, FSUM, ...)
    as anonymous functions, and DuckDB defines others as macros over an
    aggregate (GEOMEAN is ``exp(avg(ln(x)))``, JSON_GROUP_ARRAY wraps
    STRING_AGG, WAVG calls WEIGHTED_AVG). A macro is an aggregate when its
    body aggregates outside a subquery; a body sqlglot cannot read is
    ``unknown``. Read from an in-memory catalog; no data is touched.
    """
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        rows = con.execute(
            "SELECT function_name, function_type, macro_definition FROM duckdb_functions()"
        ).fetchall()
    finally:
        con.close()
    kinds: dict[str, str] = {}
    macro_bodies: dict[str, list[exp.Expression | None]] = {}
    for name, ftype, definition in rows:
        key = str(name).lower()
        if ftype == "aggregate":
            kinds[key] = "aggregate"
        elif ftype == "macro":
            try:
                body: exp.Expression | None = sqlglot.parse_one(str(definition), read="duckdb")
            except sqlglot.errors.SqlglotError:
                body = None
            macro_bodies.setdefault(key, []).append(body)
        else:
            kinds.setdefault(key, "scalar")
    # A macro may call another aggregate macro (WAVG -> WEIGHTED_AVG): iterate to a fixpoint.
    changed = True
    while changed:
        changed = False
        for key, bodies in macro_bodies.items():
            if kinds.get(key) == "aggregate":
                continue
            aggregates = any(
                body is not None
                and any(
                    isinstance(f, exp.AggFunc) or kinds.get(f.name.lower()) == "aggregate"
                    for f in _top_level_aggregates(body)
                )
                for body in bodies
            )
            if aggregates:
                kinds[key] = "aggregate"
                changed = True
    for key, bodies in macro_bodies.items():
        if key not in kinds:
            kinds[key] = "unknown" if any(body is None for body in bodies) else "scalar"
    return kinds


def _function_kind(node: exp.Anonymous) -> str:
    return _engine_function_kinds().get(node.name.lower(), "unknown")


def _is_aggregate(node: exp.Expression) -> bool:
    if isinstance(node, exp.AggFunc):
        return True
    return isinstance(node, exp.Anonymous) and _function_kind(node) == "aggregate"


def _projected_aggregate(proj: exp.Expression) -> exp.Expression | None:
    """The aggregate a projection is, under its alias, IGNORE/RESPECT NULLS and WITHIN GROUP."""
    node = proj.this if isinstance(proj, exp.Alias) else proj
    while isinstance(node, (exp.IgnoreNulls, exp.RespectNulls)) and _is_aggregate(node.this):
        node = node.this
    if isinstance(node, exp.WithinGroup) and _is_aggregate(node.this):
        return node
    return node if _is_aggregate(node) else None


def _has_column(node: exp.Expression | None) -> bool:
    return node is not None and next(node.find_all(exp.Column), None) is not None


def _aggregate_input(
    agg: exp.Expression,
) -> tuple[bool, exp.Expression | None, list[exp.Ordered]]:
    """(has_args, per-row argument or None, the aggregate's own ORDER BY).

    The argument is None when the aggregate counts rows (``*``, ``t.*``).
    ``x ORDER BY y`` and ``DISTINCT x`` are unwrapped to ``x``. The per-row
    argument is the call's ``this``; the extra arguments are parameters
    (``MAX(x, n)``, ``QUANTILE_CONT(x, q)``) and are never projected.
    """
    if isinstance(agg, exp.WithinGroup):
        return _within_group_input(agg)
    if isinstance(agg.this, exp.Expression):
        arg: exp.Expression | None = agg.this
    else:
        # Anonymous calls keep the name in ``this`` and the arguments in ``expressions``.
        arg = agg.expressions[0] if agg.expressions else None
    if arg is None:
        return False, None, []
    if isinstance(arg, exp.Limit):
        # ARRAY_AGG(x ORDER BY y LIMIT n) aggregates only some of the rows.
        raise DrillthroughError(
            "drill-through cannot derive rows for an aggregate that limits its own input"
        )
    order: list[exp.Ordered] = []
    order_columns: set[int] = set()
    if isinstance(arg, exp.Order):
        order_columns = {id(c) for o in arg.expressions for c in o.find_all(exp.Column)}
        order = [o.copy() for o in arg.expressions]
        arg = arg.this
    if isinstance(arg, exp.Distinct):
        arg = arg.expressions[0] if arg.expressions else None
    if (
        arg is None
        or isinstance(arg, exp.Star)
        or (isinstance(arg, exp.Column) and isinstance(arg.this, exp.Star))
    ):
        return True, None, order
    if not _has_column(arg):
        # ARG_MAX(3, x), CORR(0.5, x): the constant is not what varies per row. Which
        # argument carries the rows is not known, so no per-row value is projected.
        if any(id(c) not in order_columns for c in agg.find_all(exp.Column)):
            raise DrillthroughError(
                "drill-through cannot tell which argument of this aggregate holds the row values"
            )
    return True, arg.copy(), order


def _within_group_input(
    agg: exp.WithinGroup,
) -> tuple[bool, exp.Expression | None, list[exp.Ordered]]:
    """``PERCENTILE_CONT(q) WITHIN GROUP (ORDER BY x)`` aggregates ``x``, in that order."""
    order_node = agg.expression
    order = [o.copy() for o in order_node.expressions] if isinstance(order_node, exp.Order) else []
    inner = agg.this
    if isinstance(inner.this, exp.Expression) and _has_column(inner.this):
        # LISTAGG(x, sep) WITHIN GROUP (ORDER BY y): the inner call names the values.
        return True, inner.this.copy(), order
    if len(order) != 1 or not _has_column(order[0].this):
        raise DrillthroughError(
            "drill-through cannot tell which argument of this aggregate holds the row values"
        )
    return True, order[0].this.copy(), order


def _is_constant(node: exp.Expression) -> bool:
    return isinstance(node, (exp.Literal, exp.Null, exp.Boolean))


def _strip_aggregates(
    select: exp.Select,
) -> tuple[list[exp.Expression], str | None, list[exp.Ordered], bool]:
    """Replace aggregate projections with their per-row arguments.

    Returns the projections, the measure, the drill ORDER BY and whether the
    drill is approximate. The ORDER BY is the first aggregate's own ORDER BY,
    else its argument descending; it is built from nodes, never re-parsed text.
    """
    approximate = False
    measure: str | None = None
    order: list[exp.Ordered] = []
    new_exprs: list[exp.Expression] = []
    for proj in select.expressions:
        agg = _projected_aggregate(proj)
        if agg is None:
            new_exprs.append(proj.copy())
            continue
        has_args, inner, own_order = _aggregate_input(agg)
        if not has_args:
            approximate = True
            continue
        if not order and own_order:
            order = own_order
        if inner is None:
            # COUNT(*) has no per-row value; the drilled rows are what it counted.
            continue
        if measure is None and not _is_constant(inner):
            # A constant (COUNT(1)) would be a positional ORDER BY.
            measure = inner.sql(dialect="duckdb")
            if not order:
                order = [exp.Ordered(this=inner.copy(), desc=True)]
        alias = proj.alias_or_name if isinstance(proj, exp.Alias) else None
        new_exprs.append(exp.alias_(inner, alias, table=False) if alias else inner)
    return new_exprs, measure, order, approximate


def _own_aggregates(select: exp.Select, node: exp.Expression) -> list[exp.Expression]:
    """Aggregates in ``node`` that aggregate ``select`` itself.

    Window aggregates and aggregates inside a subquery are not the answer's own.
    """
    return [
        agg
        for agg in node.find_all(exp.AggFunc, exp.Anonymous)
        if _is_aggregate(agg) and agg.find_ancestor(exp.Window, exp.Select) is select
    ]


def _aggregating_subquery(node: exp.Expression) -> bool:
    """Whether ``node`` holds a subquery that aggregates (a total, a count)."""
    for sub in node.find_all(exp.Select):
        if sub is node:
            continue
        if sub.args.get("group") is not None or any(
            _own_aggregates(sub, p) for p in sub.expressions
        ):
            return True
    return False


def _outer_columns(select: exp.Select, node: exp.Expression) -> list[exp.Column]:
    """Columns in ``node`` that ``select`` itself reads (not a subquery's)."""
    return [c for c in node.find_all(exp.Column) if c.find_ancestor(exp.Select) is select]


def _int_literal(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return int(node.this)
        except ValueError:
            return None
    return None


def _table_aliases(select: exp.Select) -> list[tuple[str | None, str, str | None]]:
    """Return (alias_or_None, table_name, schema_or_None) for FROM + JOINs."""
    out: list[tuple[str | None, str, str | None]] = []
    # sqlglot 25 keys the FROM clause ``from``; later releases key it ``from_``.
    from_ = select.args.get("from_") or select.args.get("from")
    if from_ is not None:
        tbl = from_.this
        if isinstance(tbl, exp.Table):
            out.append((tbl.alias_or_name if tbl.alias else None, tbl.name, tbl.db or None))
        elif isinstance(tbl, exp.Alias) and isinstance(tbl.this, exp.Table):
            out.append((tbl.alias, tbl.this.name, tbl.this.db or None))
    for join in select.args.get("joins") or []:
        tbl = join.this
        if isinstance(tbl, exp.Table):
            out.append((tbl.alias_or_name if tbl.alias else None, tbl.name, tbl.db or None))
        elif isinstance(tbl, exp.Alias) and isinstance(tbl.this, exp.Table):
            out.append((tbl.alias, tbl.this.name, tbl.this.db or None))
    return out


def _same_column(a: exp.Column, b: exp.Column) -> bool:
    if a.name.lower() != b.name.lower():
        return False
    return not a.table or not b.table or a.table.lower() == b.table.lower()


def _same_key(node: exp.Expression, key: exp.Expression) -> bool:
    if isinstance(node, exp.Column) and isinstance(key, exp.Column):
        return _same_column(node, key)
    return node == key


def _having_as_row_filter(select: exp.Select, pred: exp.Expression) -> exp.Expression:
    """The aggregated answer's HAVING as a per-row WHERE predicate, bound as DuckDB binds it.

    A HAVING that reads only group keys keeps whole groups, so the same test on
    raw rows keeps exactly their rows. Anything else (an aggregate, an alias of
    one, a subquery, ROLLUP's NULL total row) filters groups by a value raw rows
    do not have, and moving it would serve rows the answer never used.
    """
    refused = DrillthroughError(
        "drill-through cannot derive rows for an answer whose HAVING filters on more "
        "than its group keys"
    )
    aliases: dict[str, exp.Expression] = {
        p.alias.lower(): p.this for p in select.expressions if isinstance(p, exp.Alias)
    }
    group = select.args.get("group")
    keys: list[exp.Expression] = []
    if group is not None:
        if any(v for k, v in group.args.items() if k != "expressions"):
            raise refused  # GROUP BY ALL, GROUPING SETS, ROLLUP, CUBE, WITH TOTALS
        for item in group.expressions:
            position = _int_literal(item)
            if position is not None:
                if not 1 <= position <= len(select.expressions):
                    raise refused
                proj = select.expressions[position - 1]
                item = proj.this if isinstance(proj, exp.Alias) else proj
            elif isinstance(item, exp.Column) and not item.table:
                target = aliases.get(item.name.lower())
                if target is not None and not (
                    isinstance(target, exp.Column) and _same_column(target, item)
                ):
                    # DuckDB groups by a column of this name when one exists, else
                    # by the alias; which one it was is not known here.
                    raise refused
            if isinstance(item, (exp.Rollup, exp.Cube, exp.GroupingSets)) or (
                next(item.find_all(exp.Select, exp.Window), None) is not None
                or _own_aggregates(select, item)
            ):
                raise refused
            keys.append(item)
    key_names = {k.name.lower() for k in keys if isinstance(k, exp.Column)}

    resolved = pred.copy()
    for col in list(resolved.find_all(exp.Column)):
        name = col.name.lower()
        if col.table or name in key_names or name not in aliases:
            continue
        # HAVING binds a name to a grouped column first, then to a projection alias.
        target = aliases[name].copy()
        if col is resolved:
            resolved = target
        else:
            col.replace(target)

    if next(resolved.find_all(exp.Select, exp.Window), None) is not None:
        raise refused
    for fn in resolved.find_all(exp.AggFunc, exp.Anonymous):
        if _is_aggregate(fn) or (isinstance(fn, exp.Anonymous) and _function_kind(fn) == "unknown"):
            raise refused
    for col in resolved.find_all(exp.Column):
        node: exp.Expression | None = col
        while node is not None and not any(_same_key(node, k) for k in keys):
            node = None if node is resolved else node.parent
        if node is None:
            raise refused
    return resolved


def _provenance_projections(
    aliases: list[tuple[str | None, str, str | None]],
    provenance_for: ProvenanceLookup | None = None,
) -> list[exp.Expression]:
    projs: list[exp.Expression] = []
    for alias, name, schema in aliases:
        prefix = alias or name
        present = set(provenance_for(schema, name)) if provenance_for else set(_PROVENANCE_COLS)
        for col in _PROVENANCE_COLS:
            if col not in present:
                # Projecting a column the table lacks fails the whole drill.
                continue
            # Project as prefix_col so multi-table drills stay distinct.
            qualified = exp.column(col, table=prefix)
            projs.append(exp.alias_(qualified, f"{prefix}_{col}", table=False))
    return projs


def rewrite_for_drillthrough(
    sql: str,
    *,
    dialect: str = "duckdb",
    provenance_for: ProvenanceLookup | None = None,
) -> DrillRewrite:
    """Architecture §4.5 rewrite rules.

    ``provenance_for`` names the provenance columns each table really has; when
    omitted every provenance column is projected. Any SQL the rewrite cannot
    parse or build is a DrillthroughError (a named 400), never an engine error.
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError as exc:
        raise DrillthroughError("answer SQL could not be parsed for drill-through") from exc
    try:
        return _rewrite(tree, sql, dialect=dialect, provenance_for=provenance_for)
    except sqlglot.errors.SqlglotError as exc:
        raise DrillthroughError("drill-through SQL could not be built for this answer") from exc


def _rewrite(
    tree: exp.Expression,
    sql: str,
    *,
    dialect: str,
    provenance_for: ProvenanceLookup | None,
) -> DrillRewrite:
    if not isinstance(tree, exp.Select):
        raise DrillthroughError("drill-through requires a SELECT statement")
    # sqlglot 25 keys the FROM clause ``from``; later releases key it ``from_``.
    if tree.args.get("from_") is None and tree.args.get("from") is None:
        # SELECT (SELECT MAX(x) FROM t): no table rows sit behind the answer's row.
        raise DrillthroughError("drill-through requires an answer that reads rows from a table")
    for proj in tree.expressions:
        for fn in proj.find_all(exp.Anonymous):
            if _function_kind(fn) == "unknown":
                # Not in the engine catalog (an extension or user macro): it may
                # aggregate, and a drill that misses that serves the answer value as a row.
                raise DrillthroughError(
                    f"drill-through cannot tell whether {fn.name} aggregates rows"
                )

    limit = tree.args.get("limit")
    offset = tree.args.get("offset")
    group_count_sql: str | None = None
    group_limit: int | None = None
    kept_count_sql: str | None = None
    qualify = tree.args.get("qualify")
    grouped = tree.args.get("group") is not None
    aggregated = grouped or any(_own_aggregates(tree, p) for p in tree.expressions)
    if aggregated:
        for proj in tree.expressions:
            node = proj.this if isinstance(proj, exp.Alias) else proj
            if _projected_aggregate(proj) is None and _own_aggregates(tree, node):
                # Nested (ROUND(SUM(x))) or filtered (COUNT(*) FILTER ...): the drill
                # would return the aggregate value, not the rows behind it.
                raise DrillthroughError(
                    "drill-through cannot derive rows for an aggregate inside an expression"
                )
            if _projected_aggregate(proj) is None and (
                next(node.find_all(exp.Window), None) is not None or _aggregating_subquery(node)
            ):
                # A window over groups or a subquery total is not a per-row value:
                # on drilled rows it would show numbers the answer never had there.
                raise DrillthroughError(
                    "drill-through cannot derive rows for a window or subquery total "
                    "beside an aggregate"
                )
        # A LIMIT or OFFSET that drops groups leaves rows the answer never used in
        # every other group; a drill over all groups would serve them.
        skipped = 0 if offset is None else _int_literal(offset.expression)
        kept = None if limit is None else _int_literal(limit.expression)
        if skipped != 0 or (limit is not None and (kept is None or kept < 1)):
            raise DrillthroughError(_SOME_GROUPS)
        if grouped and kept is not None:
            # Whether this LIMIT dropped a group is known only from the data.
            groups = sqlglot.parse_one(sql, read=dialect)
            groups.set("order", None)
            groups.set("limit", None)
            group_count_sql = (
                f"SELECT COUNT(*) AS group_count FROM ({groups.sql(dialect=dialect)}) AS _groups"
            )
            group_limit = kept
        if qualify is not None:
            # QUALIFY filters the answer's groups, and a window in it would rank
            # detail rows in the drill. Whether it dropped a group is known only from
            # the data: compare the groups before QUALIFY with the rows the answer kept.
            groups = sqlglot.parse_one(sql, read=dialect)
            groups.set("qualify", None)
            groups.set("order", None)
            groups.set("limit", None)
            group_count_sql = (
                f"SELECT COUNT(*) AS group_count FROM ({groups.sql(dialect=dialect)}) AS _groups"
            )
            group_limit = None
            kept_count_sql = (
                "SELECT COUNT(*) AS kept_count FROM "
                f"({sqlglot.parse_one(sql, read=dialect).sql(dialect=dialect)}) AS _kept"
            )
            tree.set("qualify", None)
    elif all(not _outer_columns(tree, p) for p in tree.expressions) and any(
        _aggregating_subquery(p) for p in tree.expressions
    ):
        # SELECT (SELECT MAX(x) FROM t) AS m FROM t LIMIT 1: the value came from rows
        # the drill would not return.
        raise DrillthroughError(
            "drill-through cannot derive rows for an answer computed by a subquery"
        )

    approximate = False
    # HAVING → WHERE when possible
    having = tree.args.get("having")
    if having is not None:
        where = tree.args.get("where")
        having_pred = _having_as_row_filter(tree, having.this) if aggregated else having.this
        if where is None:
            tree.set("where", exp.Where(this=having_pred.copy()))
        else:
            tree.set(
                "where",
                exp.Where(this=exp.and_(where.this.copy(), having_pred.copy())),
            )
        tree.set("having", None)
        # Aggregates in HAVING after strip may be wrong — flag approximate
        approximate = True

    tree.set("group", None)
    new_exprs, measure, drill_order, agg_approx = _strip_aggregates(tree)
    approximate = approximate or agg_approx

    aliases = _table_aliases(tree)
    prov = _provenance_projections(aliases, provenance_for)
    if not new_exprs and not prov:
        raise DrillthroughError("no projectable columns after stripping aggregates")
    # COUNT(*) alone still drills when its tables carry provenance columns.
    tree.set("expressions", new_exprs + prov)

    if not aggregated:
        # A row answer's contributing rows are its own rows: keep its ORDER BY,
        # OFFSET and LIMIT, capped at the drill limit.
        if limit is not None:
            kept = _int_literal(limit.expression)
            if kept is None:
                raise DrillthroughError("drill-through requires a literal LIMIT")
            drill_limit = min(kept, _DRILL_LIMIT)
        else:
            drill_limit = _DRILL_LIMIT
    elif drill_order:
        tree.set("order", exp.Order(expressions=drill_order))
        drill_limit = _DRILL_LIMIT
    else:
        # The answer's ORDER BY ranks groups (often by a stripped aggregate alias).
        tree.set("order", None)
        drill_limit = _DRILL_LIMIT
    tree.set("limit", exp.Limit(expression=exp.Literal.number(drill_limit)))

    drill_sql = tree.sql(dialect=dialect)
    # COUNT(*) over the same joins/filters without limit/order/provenance-only noise
    count_tree = sqlglot.parse_one(sql, read=dialect)
    if not isinstance(count_tree, exp.Select):
        raise DrillthroughError("count rewrite failed")
    count_tree.set("group", None)
    count_tree.set("having", None)
    count_tree.set("order", None)
    count_tree.set("limit", None)
    if aggregated:
        count_tree.set("qualify", None)
    count_tree.set(
        "expressions", [exp.alias_(exp.Count(this=exp.Star()), "contributing_count", table=False)]
    )
    # Re-apply WHERE merge if original had HAVING — already handled above for drill;
    # for count, strip aggregates from WHERE if we moved HAVING (use drill where).
    if tree.args.get("where") is not None:
        count_tree.set("where", tree.args["where"].copy())
    count_sql = count_tree.sql(dialect=dialect)
    if not aggregated and (limit is not None or offset is not None or qualify is not None):
        # Rows past the answer's own LIMIT/OFFSET, or filtered by its QUALIFY, did
        # not contribute.
        count_sql = (
            "SELECT COUNT(*) AS contributing_count FROM "
            f"({sqlglot.parse_one(sql, read=dialect).sql(dialect=dialect)}) AS _answer"
        )

    return DrillRewrite(
        sql=drill_sql,
        count_sql=count_sql,
        approximate=approximate,
        measure_expr=measure,
        group_count_sql=group_count_sql,
        group_limit=group_limit,
        kept_count_sql=kept_count_sql,
    )


def execute_drillthrough(
    token: str,
    *,
    expected_session_id: str | None = None,
    expected_manifest_hash: str | None = None,
) -> dict[str, Any]:
    """Verify token, resolve session manifest, rewrite + enforce_manifest execute."""
    import duckdb
    from cortex_contract.execution import canonical_manifest_bytes

    from CortexOS.dms.sql_validate_gate import SqlGateAbstain
    from CortexOS.execution.manifest import ManifestError
    from CortexOS.execution.pool import PoolSaturated
    from CortexOS.execution.session_manifests import (
        SessionExpired,
        SessionUnbound,
        get_session_registry,
    )
    from CortexOS.execution.submit import execute_sql

    payload = verify_token(token)
    session_id = str(payload["session_id"])
    if expected_session_id and expected_session_id != session_id:
        raise DrillthroughSessionMismatch("session_id does not match token")
    try:
        verified = get_session_registry().resolve(session_id)
    except (SessionUnbound, SessionExpired) as exc:
        raise DrillthroughSessionMismatch(str(exc)) from exc

    mhash = hashlib.sha256(canonical_manifest_bytes(verified.manifest)).hexdigest()
    token_hash = expected_manifest_hash or str(payload["manifest_hash"])
    if str(mhash) != token_hash:
        raise DrillthroughSessionMismatch("manifest_hash mismatch — refusing ACL bypass")

    sql = str(payload["sql"])
    if sql_digest(sql) != str(payload["sql_digest"]):
        raise DrillthroughTokenInvalid("sql digest mismatch")

    rewritten = rewrite_for_drillthrough(sql, provenance_for=_warehouse_provenance_columns)

    def run(drill_sql: str) -> list[dict[str, Any]]:
        try:
            out, _, _ = execute_sql(verified, drill_sql)
        except (ManifestError, PoolSaturated) as exc:
            raise DrillthroughRefused(str(exc), code=exc.code) from exc
        except (SqlGateAbstain, duckdb.Error) as exc:
            # Text is already redacted by execute_sql; raw detail stays on the cause.
            raise DrillthroughError(
                f"drill-through rows could not be derived for this answer: {exc}"
            ) from exc
        return out

    if rewritten.group_count_sql is not None:
        counted = run(rewritten.group_count_sql)
        groups = int(counted[0].get("group_count") or 0) if counted else 0
        if rewritten.kept_count_sql is not None:
            kept_rows = run(rewritten.kept_count_sql)
            limit = int(kept_rows[0].get("kept_count") or 0) if kept_rows else 0
        elif rewritten.group_limit is not None:
            limit = rewritten.group_limit
        else:
            raise DrillthroughError(_SOME_GROUPS)
        if groups > limit:
            raise DrillthroughError(_SOME_GROUPS)
    rows = run(rewritten.sql)
    try:
        count_rows, _, _ = execute_sql(verified, rewritten.count_sql)
        total = int(count_rows[0].get("contributing_count") or 0) if count_rows else len(rows)
    except Exception:  # noqa: BLE001 — count is best-effort
        total = len(rows)

    return {
        "answer_id": payload["answer_id"],
        "session_id": session_id,
        "sql_used": rewritten.sql,
        "approximate": rewritten.approximate,
        "row_count": len(rows),
        "total_count": total,
        "rows": rows,
    }


def _warehouse_provenance_columns(schema: str | None, table: str) -> list[str]:
    """Provenance columns ``table`` has in the warehouse execute_sql reads.

    Catalog metadata only, for tables the signed answer SQL already names; the
    drill SQL itself still runs through ``enforce_manifest``. On any lookup
    failure no provenance is projected: the rows stay the contributing rows.
    """
    from CortexOS.execution.warehouse import DEFAULT_DB, get_connection

    try:
        con = get_connection(DEFAULT_DB, read_only=True)
        try:
            rows = con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ? AND (? IS NULL OR table_schema = ?)",
                [table, schema, schema],
            ).fetchall()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — provenance is additive; never fail the drill on it
        return []
    present = {str(r[0]) for r in rows}
    return [c for c in _PROVENANCE_COLS if c in present]


def manifest_content_hash(manifest: Any) -> str:
    from cortex_contract.execution import canonical_manifest_bytes

    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
