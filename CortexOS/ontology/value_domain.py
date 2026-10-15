"""Declared value domains: canonical column values and the source spellings that map to them.

VALUE-NORMALIZE-CX-01 (#387). A pack's ontology may declare, on a property, the
canonical values a lake stores and the spellings a source system writes for
them (``packs/dms/ontology/object_types.yaml``: ``transactions.txn_type``
``OUT`` -> ``outbound``). The declaration is data, read once through
``CortexOS.ontology.registry``; nothing here knows a value.

Three uses, one declaration:

* ``apply_to_lake`` - every Cortex lake build (warehouse load, lakehouse silver,
  test lakes) rewrites stored source spellings to canonical values.
* ``prompt_values`` - the canonical values go into the model's generation context.
* ``canonicalize_sql`` - SQL written against source spellings (gold and
  held-out files, which never change) is run through the same mapping before it
  executes on a mapped lake. It reads parsed SQL: only a string literal compared
  with a declared column (``=``, ``<>``, ``IN``, simple ``CASE``) is rewritten. A
  comparison it cannot map exactly raises ``UnmappedLiteral`` instead of
  returning SQL that would silently match nothing.

This module does not import ``duckdb``: callers pass an open connection.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from CortexOS.ontology.registry import load_object_types

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnmappedLiteral(ValueError):
    """A source-spelled literal sits in SQL the declared mapping cannot rewrite exactly."""


@dataclass(frozen=True, slots=True)
class ValueDomain:
    table: str
    column: str
    canonical: tuple[str, ...]
    sources: tuple[tuple[str, str], ...]  # (source spelling, canonical value)

    def mapping(self) -> dict[str, str]:
        return dict(self.sources)

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"


def load_value_domains(pack_dir: Path | str | None = None) -> tuple[ValueDomain, ...]:
    """Every property that declares ``values:`` in the pack's object types."""
    out: list[ValueDomain] = []
    for obj in load_object_types(pack_dir):
        for prop in obj.properties:
            if not prop.values:
                continue
            out.append(
                ValueDomain(
                    table=obj.id,
                    column=prop.name,
                    canonical=tuple(v.canonical for v in prop.values),
                    sources=tuple((s, v.canonical) for v in prop.values for s in v.sources),
                )
            )
    return tuple(out)


def prompt_values(domains: Iterable[ValueDomain]) -> dict[str, dict[str, list[str]]]:
    """``{table: {column: [canonical, ...]}}`` for the model's generation context."""
    out: dict[str, dict[str, list[str]]] = {}
    for d in domains:
        out.setdefault(d.table, {})[d.column] = list(d.canonical)
    return out


def prompt_line(table: str, column: str, values: Sequence[str]) -> str:
    """The one wording every generation context uses for a declared domain."""
    quoted = ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)
    return f"{table}.{column} values: {quoted}"


# ── lake build ──────────────────────────────────────────────────────────────


def _ref(schema: str, table: str) -> str:
    parts = [p for p in schema.split(".") if p] if schema else []
    for part in [*parts, table]:
        if not _IDENT.match(part):
            raise ValueError(f"not a plain identifier: {part!r}")
    return ".".join([*parts, table])


def _has_column(con: Any, ref: str, column: str) -> bool:
    if not _IDENT.match(column):
        raise ValueError(f"not a plain identifier: {column!r}")
    try:
        con.execute(f"SELECT {column} FROM {ref} LIMIT 0")
    except Exception:  # noqa: BLE001 - absent table/column: nothing to map there
        return False
    return True


def _targets(
    con: Any,
    domains: Iterable[ValueDomain],
    schema: str,
    tables: Iterable[str] | None,
) -> Iterator[tuple[ValueDomain, str]]:
    only = None if tables is None else {t.lower() for t in tables}
    for d in domains:
        if not d.sources:
            continue
        if only is not None and d.table.lower() not in only:
            continue
        ref = _ref(schema, d.table)
        if _has_column(con, ref, d.column):
            yield d, ref


def source_rows(
    con: Any,
    domains: Iterable[ValueDomain],
    *,
    schema: str = "",
    tables: Iterable[str] | None = None,
) -> int:
    """Rows still holding a declared source spelling (0 on a mapped lake)."""
    total = 0
    for d, ref in _targets(con, domains, schema, tables):
        srcs = [s for s, _ in d.sources]
        marks = ", ".join("?" for _ in srcs)
        row = con.execute(
            f"SELECT COUNT(*) FROM {ref} WHERE {d.column} IN ({marks})", srcs
        ).fetchone()
        total += int(row[0]) if row else 0
    return total


def apply_to_lake(
    con: Any,
    domains: Iterable[ValueDomain],
    *,
    schema: str = "",
    tables: Iterable[str] | None = None,
) -> dict[str, int]:
    """Rewrite declared source spellings to canonical values in place.

    Returns rows rewritten per ``table.column`` for each table present. Idempotent:
    a second run rewrites nothing. A table or column the lake does not have is
    skipped, never created.
    """
    domains = tuple(domains)
    out: dict[str, int] = {}
    for d, ref in _targets(con, domains, schema, tables):
        pending = source_rows(con, (d,), schema=schema)
        if pending:
            whens = " ".join("WHEN ? THEN ?" for _ in d.sources)
            marks = ", ".join("?" for _ in d.sources)
            params: list[str] = [x for pair in d.sources for x in pair]
            params += [s for s, _ in d.sources]
            con.execute(
                f"UPDATE {ref} SET {d.column} = CASE {d.column} {whens} "
                f"ELSE {d.column} END WHERE {d.column} IN ({marks})",
                params,
            )
        out[d.key] = pending
    return out


# ── SQL written against source spellings ────────────────────────────────────


def _own(node: exp.Expression) -> Iterator[exp.Expression]:
    """Nodes under ``node`` in its own query scope (nested SELECTs are their own scope)."""
    stack = list(node.iter_expressions())
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, (exp.Select, exp.Subquery, exp.Union)):
            continue
        stack.extend(cur.iter_expressions())


def _is_str(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Literal) and node.is_string


class _Resolver:
    def __init__(self, tree: exp.Expression, domains: Sequence[ValueDomain]) -> None:
        tables = list(tree.find_all(exp.Table))
        self.present = {t.name.lower() for t in tables}
        self.alias = {t.alias_or_name.lower(): t.name.lower() for t in tables}
        self.domains = [d for d in domains if d.sources and d.table.lower() in self.present]

    def domain(self, node: exp.Expression | None) -> ValueDomain | None:
        if not isinstance(node, exp.Column):
            return None
        name = node.name.lower()
        hits = [d for d in self.domains if d.column.lower() == name]
        qual = node.table.lower()
        if qual:
            target = self.alias.get(qual)
            hits = [d for d in hits if d.table.lower() == target]
        return hits[0] if len(hits) == 1 else None

    def mentions(self, node: exp.Expression) -> bool:
        cols = [node] if isinstance(node, exp.Column) else []
        cols += [n for n in _own(node) if isinstance(n, exp.Column)]
        names = {d.column.lower() for d in self.domains}
        return any(c.name.lower() in names for c in cols)


def _swap(lit: exp.Expression | None, dom: ValueDomain, done: set[int]) -> bool:
    """Replace one string literal with its canonical value. True when it changed."""
    if lit is None or not _is_str(lit):
        return False
    done.add(id(lit))
    new = dom.mapping().get(str(lit.this))
    if new is None:
        return False
    fresh = exp.Literal.string(new)
    done.add(id(fresh))
    lit.replace(fresh)
    return True


def canonicalize_sql(sql: str, domains: Sequence[ValueDomain]) -> str:
    """Rewrite literals compared with a declared column from source to canonical spelling.

    Returns ``sql`` itself when nothing needs rewriting.
    """
    active = [d for d in domains if d.sources]
    if not sql or not active:
        return sql
    spellings = {s for d in active for s, _ in d.sources}
    if not any(f"'{s}'" in sql for s in spellings):
        return sql
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise UnmappedLiteral(f"cannot parse SQL to map declared values: {exc}") from exc
    res = _Resolver(tree, active)
    if not res.domains:
        return sql
    done: set[int] = set()
    swapped = 0

    for node in list(tree.find_all(exp.Predicate, exp.Case)):
        if isinstance(node, exp.Case):
            subject = node.this
            dom = res.domain(subject)
            if dom is not None:
                for branch in node.args.get("ifs") or []:
                    swapped += _swap(branch.this, dom, done)
            elif subject is not None and res.mentions(subject):
                raise UnmappedLiteral("CASE over a wrapped declared column")
            continue
        if not res.mentions(node) or not any(_is_str(n) for n in _own(node)):
            continue
        if isinstance(node, (exp.EQ, exp.NEQ)):
            dom_l, dom_r = res.domain(node.left), res.domain(node.right)
            dom, lit = (dom_l, node.right) if dom_l is not None else (dom_r, node.left)
            if dom is not None and _is_str(lit):
                swapped += _swap(lit, dom, done)
                continue
        if isinstance(node, exp.In) and not node.args.get("query"):
            dom = res.domain(node.this)
            items = list(node.expressions)
            if dom is not None and all(isinstance(i, exp.Literal) for i in items):
                for item in items:
                    swapped += _swap(item, dom, done)
                continue
        raise UnmappedLiteral(
            f"predicate on a declared column the mapping cannot rewrite: {node.sql('duckdb')}"
        )

    for lit in tree.find_all(exp.Literal):
        if not _is_str(lit) or id(lit) in done or str(lit.this) not in spellings:
            continue
        scope = lit.find_ancestor(exp.Select)
        if scope is not None and res.mentions(scope):
            raise UnmappedLiteral(
                f"source spelling {lit.this!r} is not compared with a declared column directly"
            )

    return tree.sql(dialect="duckdb") if swapped else sql


def canonical_gold_sql(sql: str, pack_dir: Path | str | None = None) -> str:
    """Gold SQL through the pack's declared mapping (see module docstring)."""
    return canonicalize_sql(sql, load_value_domains(pack_dir))
