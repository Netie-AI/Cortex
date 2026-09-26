"""Static check of generated SQL against a caller-declared catalog (source=space).

Shared naming rule (DMS <-> Cortex): a table crosses the boundary as its exact
qualified name ``schema.table`` (or bare, for tables that live unqualified).
Generated SQL may reference a table only by a name the caller declared:

* ``schema.table`` must be declared exactly (case-insensitive, as DuckDB is);
* a bare ``table`` is accepted when it is itself declared, or when exactly one
  declared table has that bare name; it is then rewritten to that declared
  qualified name, so the SQL that leaves Cortex reads the declared relation and
  not whatever the search path resolves;
* three-part (catalog) names, a quoted part containing a dot
  (``"bronze.schools"`` is one relation, not ``schema.table``), table functions,
  ``LATERAL``, ``WITH RECURSIVE`` and anything else are a named REFUSE.

Which references are CTEs is decided by sqlglot scope analysis
(:mod:`CortexOS.execution.sql_scope`), never by collecting ``WITH`` names from
the whole tree: a CTE inside a derived table, a forward reference to a later
sibling CTE, and a same-named CTE in another scope all leave the real table
reachable in DuckDB, so that reference must be declared like any other.

Columns are resolved by scope too and checked only for a table whose declared
column list is non-empty: a caller that sent no columns for a table gets a
table-level check for it, never a guessed allowlist. A bare column that names a
SELECT alias is exempt only as a whole ORDER BY key (the one place DuckDB binds
the alias first). The output carries the same LIMIT cap as
``sql_guardrail.validate_sql``. This is not EXPLAIN and not the manifest
enforcer; it never executes anything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _refuse(reason: str, tables: list[str] | None = None) -> dict[str, Any]:
    return {"ok": False, "sql": None, "tables": tables or [], "reason": reason, "check": "refused"}


def _bare(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def resolve_table(
    schema: str, name: str, declared: Mapping[str, Sequence[str]]
) -> tuple[str | None, str]:
    """(declared name, why-not). ``schema`` empty for a bare reference."""
    name = name.lower()
    schema = schema.lower()
    if schema:
        qualified = f"{schema}.{name}"
        if qualified in declared:
            return qualified, ""
        return None, f"table {qualified} is not in the caller catalog"
    if name in declared:
        return name, ""
    matches = sorted(t for t in declared if "." in t and _bare(t) == name)
    if len(matches) == 1:
        return matches[0], ""
    if matches:
        return None, f"bare table {name} is ambiguous in the caller catalog: " + ",".join(matches)
    return None, f"table {name} is not in the caller catalog"


def _cap_limit(stmt: Any) -> None:
    """The same LIMIT cap ``sql_guardrail.validate_sql`` applies to safe SQL."""
    from sqlglot import exp

    from CortexOS.dms.sql_guardrail import MAX_LIMIT

    limit = stmt.args.get("limit")
    value: int | None = None
    if limit is not None:
        raw = limit.expression
        if isinstance(raw, exp.Literal) and not raw.is_string:
            try:
                value = int(str(raw.this))
            except (TypeError, ValueError):
                value = None
    if value is None or value > MAX_LIMIT or value < 0:
        stmt.set("limit", exp.Limit(expression=exp.Literal.number(MAX_LIMIT)))


def _selected(scope: Any) -> dict[str, Any]:
    """This scope's FROM / JOIN sources by lower-cased name (not merely visible CTEs)."""
    return {str(k).lower(): source for k, (_node, source) in scope.selected_sources.items()}


def _source_of(scope: Any, qualifier: str) -> Any:
    """What ``qualifier`` binds to from ``scope``: this scope first, then outward."""
    current = scope
    while current is not None:
        source = _selected(current).get(qualifier)
        if source is not None:
            return source
        current = current.parent
    return None


_ABSENT = object()


def _has_star(query: Any) -> bool:
    from sqlglot import exp

    return any(
        isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
        for e in query.selects
    )


def _binds_in(
    scope: Any,
    cname: str,
    real_tables: Mapping[int, str],
    catalog: Mapping[str, Sequence[str]],
) -> Any:
    """How an unqualified ``cname`` binds among this scope's own FROM sources.

    True: to a declared column or a CTE / derived relation's named output.
    None: possibly to a table the caller declared no columns for (table-level
    check only). False: possibly to an undeclared real column -- refuse.
    ``_ABSENT``: nothing here can hold it; DuckDB looks further out.
    A relation that selects ``*`` passes its inner sources through.
    """
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope

    sources = list(_selected(scope).values())
    for rel in sources:
        if isinstance(rel, Scope) and isinstance(rel.expression, exp.Query):
            if cname in {n.lower() for n in rel.expression.named_selects}:
                return True
            if _has_star(rel.expression):
                inner = _binds_in(rel, cname, real_tables, catalog)
                if inner is not _ABSENT:
                    return inner
    reals = [real_tables.get(id(src)) for src in sources if not isinstance(src, Scope)]
    if not reals:
        return _ABSENT
    if any(t is None for t in reals):
        return False
    if any(cname in (catalog.get(t) or []) for t in reals if t):
        return True
    if any(not catalog.get(t) for t in reals if t):
        return None
    return False


def _relation_output(
    rel: Any,
    cname: str,
    real_tables: Mapping[int, str],
    catalog: Mapping[str, Sequence[str]],
) -> bool | None:
    """How ``rel.cname`` binds when ``rel`` is a CTE / derived relation.

    A named output is fine (its own scope checked what feeds it). Otherwise the
    only way in is ``*``, which passes the inner tables' columns through, so
    ``d.phone`` over ``(SELECT * FROM bronze.schools) d`` is the real ``phone``.
    """
    from sqlglot import exp

    query = rel.expression
    if not isinstance(query, exp.Query):
        return False
    if cname in {n.lower() for n in query.named_selects}:
        return True
    if _has_star(query):
        inner = _binds_in(rel, cname, real_tables, catalog)
        return False if inner is _ABSENT else inner
    return False


def _unqualified_ok(
    scope: Any,
    cname: str,
    real_tables: Mapping[int, str],
    catalog: Mapping[str, Sequence[str]],
) -> bool | None:
    """Whether an unqualified column provably binds to something declared.

    Walks outward the way DuckDB's binder does. The first scope whose sources
    can hold the name decides: a real table there may carry an undeclared
    column of this name and binds before anything further out, so a
    ``SELECT name AS ssn ... WHERE ssn = 'x'`` is checked against the declared
    columns, not waved through as an alias reference.
    """
    current = scope
    while current is not None:
        verdict = _binds_in(current, cname, real_tables, catalog)
        if verdict is not _ABSENT:
            return verdict
        current = current.parent
    return False


def _column_refusal(
    scopes: Sequence[Any],
    catalog: Mapping[str, Sequence[str]],
    real_tables: Mapping[int, str],
    tables: list[str],
) -> tuple[str | None, bool]:
    """(refusal reason or None, whether any declared-column check ran)."""
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope, walk_in_scope

    checked_any = False
    for scope in scopes:
        for col in walk_in_scope(scope.expression):
            if not isinstance(col, exp.Column) or isinstance(col.this, exp.Star):
                continue
            cname = (col.name or "").lower()
            if not cname:
                continue
            qname = col.text("table").lower()
            if qname:
                source = _source_of(scope, qname)
                if source is None:
                    return f"column qualifier {qname} does not resolve", checked_any
                if isinstance(source, Scope):
                    # A CTE / derived relation: a named output is checked in its own
                    # scope; through ``*`` the name reaches the inner tables.
                    verdict = _relation_output(source, cname, real_tables, catalog)
                    if verdict is None:
                        continue
                    checked_any = True
                    if not verdict:
                        return f"column {qname}.{cname} is not declared by the caller", checked_any
                    continue
                target = real_tables.get(id(source))
                if target is None:
                    return f"column qualifier {qname} does not resolve", checked_any
                cols = catalog.get(target) or []
                if cols:
                    checked_any = True
                    if cname not in cols:
                        return f"column {target}.{cname} is not declared by the caller", checked_any
                continue
            if _is_order_key_alias(col, scope):
                continue
            verdict = _unqualified_ok(scope, cname, real_tables, catalog)
            if verdict is None:
                continue
            checked_any = True
            if not verdict:
                return f"column {cname} is not declared for {','.join(tables)}", checked_any
    return None, checked_any


def _is_order_key_alias(col: Any, scope: Any) -> bool:
    """``ORDER BY alias``: the whole sort key is a bare select alias of this select.

    DuckDB binds an ORDER BY key to a select alias before a table column, and
    only there; in WHERE / GROUP BY / HAVING / QUALIFY / DISTINCT ON a bare name
    binds to the table column first (``SELECT name AS ssn ... WHERE ssn = 'x'``
    filters on the real ``ssn``), so those are checked like any column.
    """
    from sqlglot import exp

    ordered = col.parent
    if not isinstance(ordered, exp.Ordered) or ordered.this is not col:
        return False
    order = ordered.parent
    select = scope.expression
    if not isinstance(order, exp.Order) or order.parent is not select:
        return False
    return isinstance(select, exp.Select) and col.name.lower() in {
        n.lower() for n in select.named_selects
    }


def validate_caller_sql(sql: str, declared: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Same result shape as ``crew.freeroute.validate_sql``."""
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope

    from CortexOS.dms import sql_guardrail
    from CortexOS.execution.sql_scope import ScopeError, cte_reference_ids, dotted_part

    catalog = {str(t).lower(): [str(c).lower() for c in cols or []] for t, cols in declared.items()}
    if not catalog:
        return _refuse("caller catalog names no tables; cannot prove the SQL stays in scope")
    text = (sql or "").strip()
    if not text:
        return _refuse("empty sql")
    try:
        statements = sqlglot.parse(text, read="duckdb")
    except RecursionError:
        return _refuse("sql nests too deeply to analyse")
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal
        return _refuse(f"sql parse error: {str(exc)[:160]}")
    real = [s for s in statements if s is not None]
    if not real:
        return _refuse("empty sql")
    if len(real) > 1:
        return _refuse("more than one statement")
    stmt = real[0]
    if not isinstance(stmt, exp.Select):
        return _refuse("not a select")
    for node in stmt.walk():
        if isinstance(node, sql_guardrail.FORBIDDEN):
            return _refuse(f"non-select sql refused ({type(node).__name__.lower()})")
        if isinstance(node, exp.Command):
            return _refuse("sql contains syntax that cannot be analysed")
        if isinstance(node, exp.Lateral):
            return _refuse("lateral join refused: its name binding is not analysed here")
        if isinstance(node, exp.UDTF) and not isinstance(node, exp.Subquery):
            return _refuse("table function refused: " + node.sql(dialect="duckdb")[:80])

    # Which references are CTEs is decided by scope (WITH ordering, nesting,
    # derived tables), never by collecting WITH names from the whole tree.
    try:
        cte_refs = cte_reference_ids(stmt, allow_recursive=False)
    except ScopeError as exc:
        return _refuse(str(exc))

    resolved: list[str] = []
    real_tables: dict[int, str] = {}
    for node in list(stmt.find_all(exp.Table)):
        if not isinstance(node.this, exp.Identifier):
            return _refuse("table function refused: " + node.sql(dialect="duckdb")[:80])
        if id(node) in cte_refs:
            continue
        dotted = dotted_part(node)
        if dotted is not None:
            return _refuse(
                f"table identifier part {dotted!r} contains a dot; refused",
                sorted(resolved),
            )
        if node.catalog:
            return _refuse(
                "cross-catalog table reference refused: " + node.sql(dialect="duckdb")[:80]
            )
        name = node.name.lower()
        schema = (node.db or "").lower()
        table, why = resolve_table(schema, name, catalog)
        if table is None:
            return _refuse("sql reads a table outside the caller catalog: " + why, sorted(resolved))
        if table not in resolved:
            resolved.append(table)
        if not schema and "." in table:
            # Bare reference resolved to the one declared qualified table.
            node.set("db", exp.to_identifier(table.split(".", 1)[0]))
            if not node.alias:
                node.set("alias", exp.TableAlias(this=exp.to_identifier(node.name)))
        real_tables[id(node)] = table
    tables = sorted(resolved)
    if not tables:
        return _refuse("select has no from/join table")

    # Column check, resolved by scope. Only tables with a declared, non-empty
    # column list are column-checked; a caller that sent no columns for a table
    # gets a table-level check, never a guessed allowlist.
    try:
        scopes = traverse_scope(stmt)
    except Exception as exc:  # noqa: BLE001 - any scope failure is a refusal
        return _refuse(f"scope analysis failed ({type(exc).__name__})", tables)
    unchecked = [t for t in tables if not catalog.get(t)]
    try:
        refusal, checked_any = _column_refusal(scopes, catalog, real_tables, tables)
    except Exception as exc:  # noqa: BLE001 - an unresolvable binding is a refusal
        return _refuse(f"column scope analysis failed ({type(exc).__name__})", tables)
    if refusal is not None:
        return _refuse(refusal, tables)

    _cap_limit(stmt)
    out_sql = stmt.sql(dialect="duckdb")
    if checked_any:
        check = "caller catalog: tables and declared columns"
    else:
        check = f"table-level check only (caller declared no columns for {unchecked[0] if unchecked else tables[0]})"
    return {"ok": True, "sql": out_sql, "tables": tables, "reason": "", "check": check}


__all__ = ["resolve_table", "validate_caller_sql"]
