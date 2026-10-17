"""Output lineage for model-written SQL: does each answer column come from a table?

A model can satisfy every grant and allowlist check and still answer with a
value it typed: ``SELECT 4242 AS n FROM inventory`` reads a granted table and
returns a literal. This module decides, from the parsed SQL alone, which output
columns derive from a column of a granted base table.

An output column derives when its expression

* references a column that resolves, through CTEs, derived tables, set
  operations and stars, to a granted base table; or
* is a row count (``COUNT(*)`` or ``COUNT(<literal>)``) whose FROM and JOIN
  relations all take their rows from granted base tables (a VALUES list joined
  in makes it a count of typed rows); or
* holds a scalar subquery whose output derives.

``VALUES`` lists, a recursive CTE's own name, and any relation that is not a
base table never supply a deriving column. An alias that names two relations
(DuckDB accepts that) binds neither. An unqualified column that a
non-table source in scope (or an enclosing scope) could also expose must
derive there too (fail closed). For a UNION every branch must derive; for
INTERSECT and EXCEPT only the left branch supplies rows. No question text is
read.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from CortexOS.identifier_fold import fold_identifier

#: Violation token for an output column that does not come from a granted table.
OUTPUT_NOT_FROM_TABLE = "OUTPUT_NOT_FROM_TABLE"
#: The SQL could not be read for lineage. Fail closed.
OUTPUT_LINEAGE_UNREADABLE = "OUTPUT_LINEAGE_UNREADABLE"


# -- relations ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Base:
    name: str


@dataclass(eq=False, slots=True)
class _Query:
    node: exp.Expression
    ctes: dict[str, Any]
    parent: _Env | None


@dataclass(frozen=True, slots=True)
class _Opaque:
    """VALUES, a table function, or anything else that is not a base table."""


@dataclass(slots=True)
class _Env:
    sources: dict[str, Any]  # by alias; an alias used twice maps to _Opaque
    aliases: dict[str, exp.Expression]
    parent: _Env | None
    ctes: dict[str, Any]
    relations: list[Any]  # every FROM/JOIN relation, in order


@dataclass(slots=True)
class _Ctx:
    granted: frozenset[str]
    busy: set[tuple[str, int, str]] = field(default_factory=set)

    @contextmanager
    def guard(self, kind: str, node: exp.Expression, name: str = "") -> Iterator[bool]:
        """Yields False when this walk is already in progress (a recursive CTE)."""
        key = (kind, id(node), name)
        if key in self.busy:
            yield False
            return
        self.busy.add(key)
        try:
            yield True
        finally:
            self.busy.discard(key)


def _fold(name: object) -> str:
    return fold_identifier(str(name or ""))


def _unwrap(node: exp.Expression) -> exp.Expression:
    while isinstance(node, (exp.Subquery, exp.Paren)) and isinstance(
        node.this, (exp.Query, exp.Subquery, exp.Paren)
    ):
        node = node.this
    return node


def _is_star(item: exp.Expression) -> bool:
    return isinstance(item, exp.Star) or (
        isinstance(item, exp.Column) and isinstance(item.this, exp.Star)
    )


def _scope_ctes(node: exp.Expression, ctes: dict[str, Any], parent: _Env | None) -> dict[str, Any]:
    """CTEs visible inside ``node``: the outer ones plus its own WITH, in order."""
    with_ = node.args.get("with_") or node.args.get("with")
    if with_ is None:
        return ctes
    scope = dict(ctes)
    for cte in with_.expressions:
        name = _fold(cte.alias)
        own = dict(scope)
        rel = _Query(_unwrap(cte.this), own, parent)
        own[name] = rel  # a recursive CTE sees itself; the guard stops the loop
        scope[name] = rel
    return scope


def _relation(node: exp.Expression, ctes: dict[str, Any], parent: _Env | None) -> Any:
    if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier):
        name = _fold(node.name)
        if not node.args.get("db") and not node.args.get("catalog") and name in ctes:
            return ctes[name]
        return _Base(name)
    if isinstance(node, exp.Subquery) and isinstance(_unwrap(node.this), exp.Query):
        return _Query(_unwrap(node.this), ctes, parent)
    return _Opaque()


def _select_env(select: exp.Select, ctes: dict[str, Any], parent: _Env | None) -> _Env:
    ctes = _scope_ctes(select, ctes, parent)
    nodes: list[exp.Expression] = []
    from_ = select.args.get("from_") or select.args.get("from")
    if from_ is not None:
        nodes.append(from_.this)
    nodes.extend(join.this for join in select.args.get("joins") or [])
    sources: dict[str, Any] = {}
    relations: list[Any] = []
    for idx, node in enumerate(nodes):
        key = _fold(node.alias or (node.name if isinstance(node, exp.Table) else ""))
        rel = _relation(node, ctes, parent)
        relations.append(rel)
        key = key or f"\x00{idx}"
        # A repeated alias does not say which relation it binds: neither derives.
        sources[key] = _Opaque() if key in sources else rel
    aliases = {
        _fold(item.alias): item.this
        for item in select.expressions
        if isinstance(item, exp.Alias) and item.alias
    }
    return _Env(sources=sources, aliases=aliases, parent=parent, ctes=ctes, relations=relations)


def _branches(node: exp.Expression) -> list[exp.Expression]:
    """Set-operation branches whose rows reach the output."""
    node = _unwrap(node)
    if isinstance(node, exp.Union):
        return _branches(node.this) + _branches(node.expression)
    if isinstance(node, (exp.Intersect, exp.Except)):
        return _branches(node.this)
    return [node]


def _branch_envs(rel: _Query) -> list[tuple[exp.Select, _Env] | None]:
    ctes = _scope_ctes(rel.node, rel.ctes, rel.parent)
    out: list[tuple[exp.Select, _Env] | None] = []
    for branch in _branches(rel.node):
        if isinstance(branch, exp.Select):
            out.append((branch, _select_env(branch, ctes, rel.parent)))
        else:
            out.append(None)
    return out


# -- lineage ----------------------------------------------------------------------


def _item_derives(item: exp.Expression, env: _Env, ctx: _Ctx) -> bool:
    if isinstance(item, exp.Star):
        return bool(env.relations) and all(_fully_derives(r, ctx) for r in env.relations)
    if isinstance(item, exp.Column) and isinstance(item.this, exp.Star):
        rel = _lookup(env, _fold(item.table))
        return rel is not None and _fully_derives(rel, ctx)
    return _expr_derives(item, env, ctx, own=_fold(item.alias_or_name), seen=frozenset())


def _outputs(rel: _Query, ctx: _Ctx) -> list[tuple[str, bool]] | None:
    """``[(name, derives)]`` by position, or None when a star hides the positions."""
    with ctx.guard("outputs", rel.node) as fresh:
        if not fresh:
            return [("", False)]
        branches = _branch_envs(rel)
        if branches[0] is None:
            return [("", False)]
        first, first_env = branches[0]
        if any(_is_star(item) for item in first.expressions):
            return None
        merged: list[tuple[str, bool]] = []
        for pos, item in enumerate(first.expressions):
            ok = _item_derives(item, first_env, ctx)
            for other in branches[1:]:
                if not ok:
                    break
                if other is None or pos >= len(other[0].expressions):
                    ok = False
                    continue
                o_select, o_env = other
                o_item = o_select.expressions[pos]
                if any(_is_star(x) for x in o_select.expressions):
                    ok = all(_item_derives(x, o_env, ctx) for x in o_select.expressions)
                else:
                    ok = _item_derives(o_item, o_env, ctx)
            merged.append((_fold(item.alias_or_name), ok))
        return merged


def _fully_derives(rel: Any, ctx: _Ctx) -> bool:
    """Every column ``rel`` exposes derives."""
    if isinstance(rel, _Base):
        return rel.name in ctx.granted
    if not isinstance(rel, _Query):
        return False
    with ctx.guard("full", rel.node) as fresh:
        if not fresh:
            return False
        for entry in _branch_envs(rel):
            if entry is None:
                return False
            select, env = entry
            if not all(_item_derives(item, env, ctx) for item in select.expressions):
                return False
        return True


def _column_of(rel: Any, name: str, ctx: _Ctx) -> bool:
    """True when ``rel`` exposes ``name`` and that column derives."""
    if isinstance(rel, _Base):
        return rel.name in ctx.granted
    if not isinstance(rel, _Query):
        return False
    with ctx.guard("column", rel.node, name) as fresh:
        if not fresh:
            return False
        branches = _branch_envs(rel)
        if branches[0] is None:
            return False
        first, first_env = branches[0]
        for pos, item in enumerate(first.expressions):
            if not _is_star(item) and _fold(item.alias_or_name) == name:
                outs = _outputs(rel, ctx)
                if outs is not None:
                    return outs[pos][1]
                return _item_derives(item, first_env, ctx) and all(
                    other is not None
                    and all(_item_derives(x, other[1], ctx) for x in other[0].expressions)
                    for other in branches[1:]
                )
        # Not named in the select list: only a star can supply it.
        stars: list[Any] = []
        for item in first.expressions:
            if isinstance(item, exp.Star):
                stars.extend(first_env.sources.values())
            elif isinstance(item, exp.Column) and isinstance(item.this, exp.Star):
                stars.append(_lookup(first_env, _fold(item.table)) or _Opaque())
        if not stars or not all(_column_of(src, name, ctx) for src in stars):
            return False
        return all(
            other is not None and all(_item_derives(x, other[1], ctx) for x in other[0].expressions)
            for other in branches[1:]
        )


def _exposes(rel: Any, name: str) -> bool:
    """Could ``rel`` supply an unqualified column ``name``? A base table: not decided here."""
    if isinstance(rel, _Opaque):
        return True
    if not isinstance(rel, _Query):
        return False
    first = _branches(rel.node)[0]
    if not isinstance(first, exp.Select):
        return True
    return any(_is_star(item) or _fold(item.alias_or_name) == name for item in first.expressions)


def _rows_from_granted(env: _Env, ctx: _Ctx) -> bool:
    """Every relation in this FROM/JOIN takes its rows from granted base tables."""
    return bool(env.relations) and all(_reads_granted(r, ctx) for r in env.relations)


def _reads_granted(rel: Any, ctx: _Ctx) -> bool:
    """Every row of ``rel`` comes from granted base tables (VALUES rows do not)."""
    if isinstance(rel, _Base):
        return rel.name in ctx.granted
    if not isinstance(rel, _Query):
        return False
    with ctx.guard("reads", rel.node) as fresh:
        if not fresh:
            return False
        return all(
            entry is not None and _rows_from_granted(entry[1], ctx) for entry in _branch_envs(rel)
        )


def _lookup(env: _Env | None, name: str) -> Any:
    while env is not None:
        if name in env.sources:
            return env.sources[name]
        env = env.parent
    return None


def _column_derives(col: exp.Column, env: _Env, ctx: _Ctx, own: str, seen: frozenset[str]) -> bool:
    name = _fold(col.name)
    if col.table:
        rel = _lookup(env, _fold(col.table))
        return rel is not None and _column_of(rel, name, ctx)
    # Unqualified: a derived relation or VALUES that could expose the name, in
    # this scope or an enclosing one, must derive it. Otherwise a granted base
    # table in scope supplies it.
    shadows: list[Any] = []
    granted_base = False
    scope: _Env | None = env
    while scope is not None:
        for rel in scope.relations:
            if _exposes(rel, name):
                shadows.append(rel)
            elif isinstance(rel, _Base) and rel.name in ctx.granted:
                granted_base = True
        scope = scope.parent
    ok = all(_column_of(rel, name, ctx) for rel in shadows) if shadows else granted_base
    # DuckDB may bind a same-select alias of that name instead. Both readings
    # must derive.
    if ok and name != own and name in env.aliases and name not in seen:
        ok = _expr_derives(env.aliases[name], env, ctx, own=own, seen=seen | {name})
    return ok


def _is_row_count(node: exp.Expression) -> bool:
    return isinstance(node, exp.Count) and isinstance(node.this, (exp.Star, exp.Literal))


def _expr_derives(
    expr: exp.Expression, env: _Env, ctx: _Ctx, *, own: str, seen: frozenset[str]
) -> bool:
    stack: list[exp.Expression] = [expr]
    while stack:
        node = stack.pop()
        if node is not expr and isinstance(node, (exp.Query, exp.Subquery)):
            inner = _unwrap(node)
            if isinstance(inner, exp.Query):
                outs = _outputs(_Query(inner, env.ctes, env), ctx)
                if outs and outs[0][1]:
                    return True
            continue
        if isinstance(node, exp.Column) and not isinstance(node.this, exp.Star):
            if _column_derives(node, env, ctx, own, seen):
                return True
            continue
        if _is_row_count(node) and _rows_from_granted(env, ctx):
            return True
        stack.extend(node.iter_expressions())
    return False


# -- public -----------------------------------------------------------------------


def underived_outputs(sql: str, granted: Iterable[str]) -> list[str]:
    """Output column names that do not derive from a granted base table.

    Empty when every output derives. ``[OUTPUT_LINEAGE_UNREADABLE]`` when the
    SQL cannot be parsed or is not a query: that is not proof of lineage.
    ``granted`` holds the grant's table names; they are folded as DuckDB folds.
    """
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001 - unreadable SQL is not proof of lineage
        return [OUTPUT_LINEAGE_UNREADABLE]
    if tree is None:
        return [OUTPUT_LINEAGE_UNREADABLE]
    tree = _unwrap(tree)
    if not isinstance(tree, exp.Query):
        return [OUTPUT_LINEAGE_UNREADABLE]
    ctx = _Ctx(granted=frozenset(_fold(name) for name in granted))
    rel = _Query(tree, {}, None)
    outs = _outputs(rel, ctx)
    if outs is None:
        return [] if _fully_derives(rel, ctx) else ["*"]
    return [name or "?" for name, ok in outs if not ok]


def lineage_violations(sql: str, granted: Iterable[str]) -> list[str]:
    """``OUTPUT_NOT_FROM_TABLE:<name>`` per output that does not derive. Empty when all do."""
    bad = underived_outputs(sql, granted)
    if bad == [OUTPUT_LINEAGE_UNREADABLE]:
        return bad
    return [f"{OUTPUT_NOT_FROM_TABLE}:{name}" for name in bad]


__all__ = [
    "OUTPUT_LINEAGE_UNREADABLE",
    "OUTPUT_NOT_FROM_TABLE",
    "lineage_violations",
    "underived_outputs",
]
