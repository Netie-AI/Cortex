"""Static check of generated SQL against a caller-declared catalog (source=space).

Shared naming rule (DMS <-> Cortex): a table crosses the boundary as its exact
qualified name ``schema.table`` (or bare, for tables that live unqualified).
Generated SQL may reference a table only by a name the caller declared:

* ``schema.table`` must be declared exactly (case-insensitive, as DuckDB is);
* a bare ``table`` is accepted when it is itself declared, or when exactly one
  declared table has that bare name; it is then rewritten to that declared
  qualified name, so the SQL that leaves Cortex reads the declared relation and
  not whatever the search path resolves;
* three-part (catalog) names, table functions and anything else are a named
  REFUSE.

Columns are checked only for a table whose declared column list is non-empty:
a caller that sent no columns for a table gets a table-level check for it, never
a guessed allowlist. This is not EXPLAIN and not the manifest enforcer; it never
executes anything.
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


def validate_caller_sql(sql: str, declared: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Same result shape as ``crew.freeroute.validate_sql``."""
    import sqlglot
    from sqlglot import exp

    from CortexOS.dms import sql_guardrail

    catalog = {str(t).lower(): [str(c).lower() for c in cols or []] for t, cols in declared.items()}
    if not catalog:
        return _refuse("caller catalog names no tables; cannot prove the SQL stays in scope")
    text = (sql or "").strip()
    if not text:
        return _refuse("empty sql")
    try:
        statements = sqlglot.parse(text, read="duckdb")
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

    ctes = {c.alias.lower() for c in stmt.find_all(exp.CTE) if c.alias}
    resolved: list[str] = []
    rewrote = False
    alias_to_table: dict[str, str] = {}
    for node in list(stmt.find_all(exp.Table)):
        if not isinstance(node.this, exp.Identifier):
            return _refuse("table function refused: " + node.sql(dialect="duckdb")[:80])
        name = node.name.lower()
        schema = (node.db or "").lower()
        if node.catalog:
            return _refuse(
                "cross-catalog table reference refused: " + node.sql(dialect="duckdb")[:80]
            )
        if not schema and name in ctes:
            continue
        table, why = resolve_table(schema, name, catalog)
        if table is None:
            return _refuse("sql reads a table outside the caller catalog: " + why, sorted(resolved))
        if table not in resolved:
            resolved.append(table)
        if not schema and "." in table:
            # Bare reference resolved to the one declared qualified table.
            node.set("db", exp.to_identifier(table.split(".", 1)[0]))
            if not node.alias:
                node.set("alias", exp.TableAlias(this=exp.to_identifier(name)))
            rewrote = True
        alias_to_table[(node.alias or name).lower()] = table
        alias_to_table.setdefault(name, table)
    tables = sorted(resolved)
    if not tables:
        return _refuse("select has no from/join table")

    # Column check: only for tables with a declared, non-empty column list.
    derived = {a.name.lower() for a in stmt.find_all(exp.TableAlias) if a.name} - set(alias_to_table)
    output_names = {a.alias.lower() for a in stmt.find_all(exp.Alias) if a.alias}
    unchecked = [t for t in tables if not catalog.get(t)]
    union = {c for t in tables for c in catalog.get(t, [])}
    checked_any = False
    for col in stmt.find_all(exp.Column):
        cname = (col.name or "").lower()
        if not cname or cname == "*":
            continue
        qual = col.args.get("table")
        qname = qual.name.lower() if qual is not None else ""
        if qname:
            target = alias_to_table.get(qname)
            if target is None or qname in derived or qname in ctes:
                continue  # a CTE / derived relation this statement defines itself
            cols = catalog.get(target) or []
            if cols:
                checked_any = True
                if cname not in cols:
                    return _refuse(f"column {target}.{cname} is not declared by the caller", tables)
            continue
        if unchecked or cname in output_names:
            continue
        checked_any = True
        if cname not in union:
            return _refuse(f"column {cname} is not declared for {','.join(tables)}", tables)

    out_sql = stmt.sql(dialect="duckdb") if rewrote else text
    if checked_any:
        check = "caller catalog: tables and declared columns"
    else:
        check = f"table-level check only (caller declared no columns for {unchecked[0] if unchecked else tables[0]})"
    return {"ok": True, "sql": out_sql, "tables": tables, "reason": "", "check": check}


__all__ = ["resolve_table", "validate_caller_sql"]
