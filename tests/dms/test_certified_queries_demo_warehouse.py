"""Every certified query must return rows on the synthetic demo warehouse
when that warehouse has rows for the query once absent string literals are
ignored. Queries are read from certified_queries.yaml. No query id is named
here.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from sqlglot import exp, parse_one

from tests.dms.synthetic_demo_warehouse import (
    open_mixed_convention_warehouse,
    open_synthetic_demo_warehouse,
)

_YAML = Path(__file__).resolve().parents[2] / "packs" / "dms" / "semantic" / "certified_queries.yaml"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _certified() -> list[dict]:
    doc = yaml.safe_load(_YAML.read_text(encoding="utf-8")) or {}
    rows = doc.get("certified") or []
    return [row for row in rows if isinstance(row, dict) and str(row.get("sql") or "").strip()]


def _alias_map(tree: exp.Expression) -> tuple[dict[str, str], list[str]]:
    alias_to_table: dict[str, str] = {}
    tables: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name
        if not name or not _IDENT.match(name):
            continue
        alias = table.alias_or_name
        alias_to_table[alias] = name
        alias_to_table[name] = name
        if name not in tables:
            tables.append(name)
    return alias_to_table, tables


def _distinct(con, table: str, column: str) -> set[object] | None:
    if not _IDENT.match(table) or not _IDENT.match(column):
        return None
    try:
        rows = con.execute(f'SELECT DISTINCT "{column}" FROM "{table}"').fetchall()
    except Exception:
        return None
    return {row[0] for row in rows if row[0] is not None}


def _column_values(con, column: exp.Column, alias_to_table: dict[str, str], tables: list[str]) -> set[object] | None:
    qualifier = column.table
    if qualifier:
        table = alias_to_table.get(qualifier)
        targets = [table] if table else []
    else:
        targets = tables
    found: set[object] = set()
    seen = False
    for table in targets:
        values = _distinct(con, table, column.name)
        if values is None:
            continue
        seen = True
        found |= values
    if not seen:
        return None
    return found


def _string_lits(node: exp.Expression) -> list[str]:
    if isinstance(node, exp.Literal) and node.is_string:
        return [node.this]
    return []


def _predicate_misses_data(con, node: exp.Expression, alias_to_table: dict[str, str], tables: list[str]) -> bool:
    if isinstance(node, exp.EQ) and isinstance(node.this, exp.Column):
        lits = _string_lits(node.expression)
        if not lits:
            return False
        values = _column_values(con, node.this, alias_to_table, tables)
        if values is None:
            return False
        return lits[0] not in values
    if isinstance(node, exp.In) and isinstance(node.this, exp.Column):
        lits: list[str] = []
        for item in node.expressions:
            lits.extend(_string_lits(item))
        if not lits:
            return False
        values = _column_values(con, node.this, alias_to_table, tables)
        if values is None:
            return False
        return not any(lit in values for lit in lits)
    return False


def _oracle_rows(con, sql: str) -> list:
    """Rows the query would return with string literals that miss the column removed.

    When every literal is present, the oracle is the query itself.
    """
    tree = parse_one(sql, read="duckdb")
    where = tree.args.get("where")
    if where is None:
        return con.execute(sql).fetchall()
    alias_to_table, tables = _alias_map(tree)
    changed = False
    for node in list(where.find_all(exp.EQ, exp.In)):
        if _predicate_misses_data(con, node, alias_to_table, tables):
            node.replace(exp.true())
            changed = True
    if not changed:
        return con.execute(sql).fetchall()
    return con.execute(tree.sql(dialect="duckdb")).fetchall()


def _rows(con, sql: str) -> list[tuple]:
    rows = [tuple(row) for row in con.execute(sql).fetchall()]
    return sorted(rows, key=lambda row: tuple(repr(value) for value in row))


def test_certified_queries_return_rows_when_demo_warehouse_has_rows() -> None:
    misses: list[str] = []
    con = open_synthetic_demo_warehouse()
    try:
        queries = _certified()
        assert queries, f"no certified sql in {_YAML.name}"
        for query in queries:
            sql = str(query.get("sql") or "")
            label = str(query.get("id") or sql[:40])
            try:
                got = con.execute(sql).fetchall()
                oracle = _oracle_rows(con, sql)
            except Exception as exc:
                misses.append(f"{label}: query failed: {exc}")
                continue
            # Empty oracle: the demo seed has no rows for this query. Not a miss.
            if not oracle:
                continue
            if not got:
                misses.append(
                    f"{label}: 0 rows but oracle has {len(oracle)} rows"
                )
    finally:
        con.close()
    assert not misses, "certified queries missed demo rows:\n" + "\n".join(misses)


def test_certified_queries_do_not_count_old_txn_type_spelling() -> None:
    """Same measures stored as the old spelling must not change any certified result."""
    misses: list[str] = []
    canonical = open_synthetic_demo_warehouse()
    mixed = open_mixed_convention_warehouse()
    try:
        queries = _certified()
        assert queries, f"no certified sql in {_YAML.name}"
        for query in queries:
            sql = str(query.get("sql") or "")
            label = str(query.get("id") or sql[:40])
            try:
                canon_rows = _rows(canonical, sql)
                mixed_rows = _rows(mixed, sql)
            except Exception as exc:
                misses.append(f"{label}: query failed: {exc}")
                continue
            if canon_rows != mixed_rows:
                misses.append(
                    f"{label}: canonical {canon_rows[:1]} != mixed {mixed_rows[:1]} "
                    f"({len(canon_rows)} vs {len(mixed_rows)} rows)"
                )
    finally:
        canonical.close()
        mixed.close()
    assert not misses, "certified queries counted a non-canonical txn_type:\n" + "\n".join(misses)
