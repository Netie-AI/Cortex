"""Declared value domains: canonical column values and the source spellings that map to them.

VALUE-NORMALIZE-CX-01 (#387). A pack's ontology may declare, on a property, the
canonical values a lake stores and the spellings a source system writes for
them (``packs/dms/ontology/object_types.yaml``: ``transactions.txn_type``
``OUT`` -> ``outbound``). The declaration is data, read once through
``CortexOS.ontology.registry``; nothing here knows a value.

Four uses, one declaration:

* ``apply_to_lake`` - every Cortex lake build (warehouse load, lakehouse silver,
  test lakes) rewrites stored source spellings to canonical values.
  ``undeclared_values`` then counts and names any stored value the declaration
  does not list; such a value is reported, never guessed at or mapped.
* ``stale_columns`` - at query time (``execute_sql`` and ``guard_and_execute``,
  the seams every served answer runs through), before SQL that names a
  declared column fetches a row, the lake it runs on is checked for stored
  values a canonical filter or grouping would misread: a declared source
  spelling the build did not map (a lake built before the declaration, or by
  another loader), or a value the declaration does not list. Either one is a
  named abstain (``lake_value_domain_stale:<table>.<column>``), never a served
  zero.
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

import logging
import re
import threading
from collections.abc import Hashable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from CortexOS.ontology.registry import load_object_types, object_types_path

_log = logging.getLogger(__name__)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Named abstain reason: the lake does not store the declared values of a column.
LAKE_STALE = "lake_value_domain_stale"


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


_DECLARED: dict[str, tuple[tuple[int, int], tuple[ValueDomain, ...]]] = {}
_DECLARED_LOCK = threading.Lock()


def declared_value_domains(pack_dir: Path | str) -> tuple[ValueDomain, ...]:
    """``load_value_domains`` for a serving seam that asks on every query.

    Parsed once and kept until the pack's ontology file changes (size or
    modification time). A pack with no ontology file declares nothing: ``()``.
    A malformed declaration still raises, as ``load_value_domains`` does.
    """
    path = object_types_path(pack_dir)
    try:
        st = path.stat()
    except FileNotFoundError:
        return ()
    key, stamp = str(path.resolve()), (st.st_mtime_ns, st.st_size)
    with _DECLARED_LOCK:
        hit = _DECLARED.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    domains = load_value_domains(pack_dir)
    with _DECLARED_LOCK:
        _DECLARED[key] = (stamp, domains)
    return domains


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
    *,
    mapped_only: bool = True,
) -> Iterator[tuple[ValueDomain, str]]:
    only = None if tables is None else {t.lower() for t in tables}
    for d in domains:
        if mapped_only and not d.sources:
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


def undeclared_values(
    con: Any,
    domains: Iterable[ValueDomain],
    *,
    schema: str = "",
    tables: Iterable[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Stored values the declaration does not name: ``{table.column: {value: rows}}``.

    The declared list is the complete domain: a value that is neither a
    canonical value nor a declared source spelling is one ``apply_to_lake``
    leaves as it is, so a filter on the canonical value silently misses it.
    Nothing here guesses what such a value means (no case folding, no
    synonyms); it is counted and named so the build can report it. Empty when
    every non-null value is declared. Comparison is on the stored text.
    """
    out: dict[str, dict[str, int]] = {}
    for d, ref in _targets(con, domains, schema, tables, mapped_only=False):
        known = [*d.canonical, *(s for s, _ in d.sources)]
        marks = ", ".join("?" for _ in known)
        rows = con.execute(
            f"SELECT CAST({d.column} AS VARCHAR), COUNT(*) FROM {ref} "
            f"WHERE {d.column} IS NOT NULL "
            f"AND CAST({d.column} AS VARCHAR) NOT IN ({marks}) "
            "GROUP BY 1 ORDER BY 1",
            known,
        ).fetchall()
        if rows:
            out[d.key] = {str(v): int(n) for v, n in rows}
    return out


# ── serve time: does the lake hold what the declaration says? ───────────────


@dataclass(frozen=True, slots=True)
class LakeDrift:
    """Rows of one declared column that a filter or grouping on canonical values misreads.

    Counts only. The stored values themselves are never carried here, so no
    reason built from this can echo a cell value to a user or a model.
    """

    key: str
    unmapped_rows: int  # a declared source spelling the lake build did not map
    undeclared_rows: int  # a value the declaration does not list at all

    @property
    def code(self) -> str:
        return f"{LAKE_STALE}:{self.key}"

    def reason(self) -> str:
        what: list[str] = []
        if self.unmapped_rows:
            what.append("declared source spellings its build did not map")
        if self.undeclared_rows:
            what.append("values the declaration does not list")
        return (
            f"{self.code}: the lake stores {' and '.join(what)}, so a filter or "
            "grouping on that column would not match the declared values (rebuild "
            "the lake through the declaration)"
        )


def domains_in_sql(sql: str, domains: Iterable[ValueDomain]) -> tuple[ValueDomain, ...]:
    """Declared domains whose column ``sql`` names, on a table ``sql`` reads.

    Matched by column name whenever the domain's table is read anywhere in the
    statement, so a reference through a CTE, a derived table or an alias counts:
    the check reads more columns rather than miss one. SQL that does not parse
    is treated as naming every declared domain (fail closed). ``SELECT *`` names
    no column: a listing shows what is stored and filters nothing.
    """
    domains = tuple(domains)
    if not sql or not domains:
        return ()
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return domains
    if tree is None:
        return domains
    tables = {t.name.lower() for t in tree.find_all(exp.Table)}
    columns = {c.name.lower() for c in tree.find_all(exp.Column)}
    return tuple(
        d for d in domains if d.table.lower() in tables and d.column.lower() in columns
    )


_LAKE_CACHE: dict[tuple[Hashable, str, ValueDomain], tuple[int, int] | None] = {}
_LAKE_CACHE_MAX = 256
_LAKE_LOCK = threading.Lock()


def clear_lake_cache() -> None:
    """Forget every cached lake check (tests). A rebuild needs no call: it is a new instance."""
    with _LAKE_LOCK:
        _LAKE_CACHE.clear()


def _drift_counts(con: Any, d: ValueDomain, schema: str) -> tuple[int, int] | None:
    """(unmapped rows, undeclared rows) for one column; None when the lake lacks it."""
    ref = _ref(schema, d.table)
    if not _has_column(con, ref, d.column):
        return None
    srcs = [s for s, _ in d.sources]
    known = [*d.canonical, *srcs]
    unmapped = (
        f"COUNT(*) FILTER (WHERE v IN ({', '.join('?' for _ in srcs)}))" if srcs else "0"
    )
    row = con.execute(
        f"SELECT {unmapped}, "
        f"COUNT(*) FILTER (WHERE v NOT IN ({', '.join('?' for _ in known)})) "
        f"FROM (SELECT CAST({d.column} AS VARCHAR) AS v FROM {ref} "
        f"WHERE {d.column} IS NOT NULL) AS _declared",
        [*srcs, *known],
    ).fetchone()
    if not row:
        return (0, 0)
    return (int(row[0] or 0), int(row[1] or 0))


def lake_drift(
    con: Any,
    domains: Iterable[ValueDomain],
    *,
    schema: str = "",
    instance: Hashable | None = None,
) -> tuple[LakeDrift, ...]:
    """Declared columns in the lake ``con`` reads that hold values the declaration does not.

    One aggregate scan per column. ``instance`` names the database instance
    ``con`` reads, one whose data cannot change while it lives (the serving
    warehouse's cached read-only instance: ``warehouse.connection_instance``);
    the result is kept per instance, so it is scanned once, and a later instance
    on the same path (a rebuild, a file renamed over it) is scanned afresh. With
    no ``instance`` nothing is kept and every call scans: a path or a file stamp
    does not say which file an open connection reads, and a verdict filed under
    one outlives the connection it was about. A table or column the lake does
    not have is skipped.
    """
    out: list[LakeDrift] = []
    for d in domains:
        key = None if instance is None else (instance, schema, d)
        with _LAKE_LOCK:
            hit = key is not None and key in _LAKE_CACHE
            counts = _LAKE_CACHE[key] if hit and key is not None else None
        if not hit:
            counts = _drift_counts(con, d, schema)
            if key is not None:
                with _LAKE_LOCK:
                    if len(_LAKE_CACHE) >= _LAKE_CACHE_MAX:
                        _LAKE_CACHE.clear()
                    _LAKE_CACHE[key] = counts
        if counts is not None and any(counts):
            out.append(LakeDrift(d.key, *counts))
    return tuple(out)


def stale_columns(
    con: Any,
    sql: str,
    domains: Iterable[ValueDomain],
    *,
    schema: str = "",
    instance: Hashable | None = None,
) -> tuple[LakeDrift, ...]:
    """Declared columns ``sql`` names that the lake does not hold as declared.

    Empty when ``sql`` names no declared column, or when every one it names
    stores only declared canonical values. A non-empty result is a named
    abstain (``stale_reason``); it names columns and row counts, never a value.
    ``instance`` is as for ``lake_drift``.
    """
    named = domains_in_sql(sql, domains)
    if not named:
        return ()
    drift = lake_drift(con, named, schema=schema, instance=instance)
    for item in drift:
        _log.warning(
            "%s unmapped_rows=%d undeclared_rows=%d instance=%s",
            item.code,
            item.unmapped_rows,
            item.undeclared_rows,
            instance if instance is not None else "<unnamed>",
        )
    return drift


def stale_reason(drift: Iterable[LakeDrift]) -> str:
    """The user-visible abstain reason for ``stale_columns``: column names, no values."""
    return "; ".join(item.reason() for item in drift)


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
