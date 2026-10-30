"""Declared measures: what a number means, read from the pack ontology.

METRIC-SEMANTICS-01. A pack's ontology may declare measures on an object
(``packs/dms/ontology/object_types.yaml``: ``transactions.sales_value_myr`` is
``SUM(quantity_kg * unit_cost_myr)`` over rows where ``txn_type = 'outbound'``).
The filter value must be a declared canonical value of its column
(``CortexOS.ontology.value_domain``), so the definition and the lake agree on
the spelling. Nothing here knows a measure, a table or a value.

Two uses, one declaration:

* ``prompt_lines`` - the definitions for the tables in play, rendered into the
  model's SQL-writing context as data.
* ``check_sql`` - a structural check over parsed SQL. An aggregate (the
  declared one, or ``AVG`` of a ``SUM`` measure) whose arguments cover a
  measure's columns on the measure's table must pin the filter column with
  ``=`` or ``IN`` on string literals: in the ``WHERE`` of its own query or of
  the CTE / derived table the rows come from, in an inner (or the measure
  side of a left) join condition, or inside the aggregate (``FILTER`` or
  ``CASE``); or the aggregate must be grouped (``GROUP BY`` / ``PARTITION BY``)
  by the filter column, which keeps one value per kind. An unpinned aggregate is ``measure_filter_missing``; one that only
  excludes the required value is ``measure_filter_inverted``. A positive pin to
  other declared values is the SQL's explicit choice and passes (inbound totals
  are legitimate). A positive pin to a literal outside the column's declared
  canonical values (a source spelling such as ``OUT``, another case, a word the
  declaration does not list) is ``measure_filter_undeclared``: the lake stores
  only canonical values, so that filter sums no rows. The check never reads
  question text.

A column the check cannot trace to a table (an outer reference, a set
operation it cannot line up) is not treated as a measure column; the static
validator in front of it has already scoped the tables.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from CortexOS.ontology.registry import load_object_types

MISSING = "measure_filter_missing"
INVERTED = "measure_filter_inverted"
UNDECLARED = "measure_filter_undeclared"
_MAX_LITERAL = 40


@dataclass(frozen=True, slots=True)
class Measure:
    table: str
    name: str
    description: str
    expression: str
    columns: tuple[str, ...]
    filter_column: str
    filter_value: str
    domain: tuple[str, ...]  # declared canonical values of filter_column
    grain: str
    metrics: tuple[str, ...]
    aggregates: tuple[type[exp.Expression], ...]

    @property
    def key(self) -> str:
        return f"{self.table}.{self.name}"


def _expression_parts(
    where: str, text: str, props: set[str]
) -> tuple[tuple[str, ...], tuple[type[exp.Expression], ...]]:
    try:
        parsed = sqlglot.parse(text, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise ValueError(f"{where}: expression does not parse: {exc}") from exc
    if len(parsed) != 1 or not isinstance(parsed[0], exp.AggFunc):
        raise ValueError(f"{where}: expression must be one aggregate, e.g. SUM(col)")
    node = parsed[0]
    if node.find(exp.Select) is not None:
        raise ValueError(f"{where}: expression must not hold a query")
    columns: list[str] = []
    for col in node.find_all(exp.Column):
        name = col.name
        if name not in props:
            raise ValueError(f"{where}: expression column {name!r} is not a property")
        if name not in columns:
            columns.append(name)
    if not columns:
        raise ValueError(f"{where}: expression names no column")
    aggs: tuple[type[exp.Expression], ...] = (type(node),)
    if isinstance(node, exp.Sum):
        aggs = (exp.Sum, exp.Avg)  # the mean of a summed measure is the same measure
    return tuple(columns), aggs


def load_measures(pack_dir: Path | str | None = None) -> tuple[Measure, ...]:
    """Every measure the pack's object types declare, with its expression parsed."""
    out: list[Measure] = []
    for obj in load_object_types(pack_dir):
        props = {p.name: p for p in obj.properties}
        for d in obj.measures:
            columns, aggs = _expression_parts(f"{obj.id}.{d.name}", d.expression, set(props))
            out.append(
                Measure(
                    table=obj.id,
                    name=d.name,
                    description=d.description,
                    expression=d.expression,
                    columns=columns,
                    filter_column=d.filter_column,
                    filter_value=d.filter_value,
                    domain=tuple(v.canonical for v in props[d.filter_column].values),
                    grain=d.grain,
                    metrics=d.metrics,
                    aggregates=aggs,
                )
            )
    return tuple(out)


def ontology_schema(pack_dir: Path | str | None = None) -> dict[str, dict[str, str]]:
    """``{table: {column: type}}`` from the object types, for column qualification."""
    kinds = {"number": "DOUBLE", "integer": "BIGINT", "boolean": "BOOLEAN"}
    return {
        obj.id: {p.name: kinds.get(p.type, "VARCHAR") for p in obj.properties}
        for obj in load_object_types(pack_dir)
    }


def _quote(value: str) -> str:
    return "'" + str(value)[:_MAX_LITERAL].replace("'", "''") + "'"


def definition(m: Measure) -> str:
    """The one wording every generation context uses for a declared measure."""
    line = (
        f"measure {m.key} = {m.expression} "
        f"WHERE {m.table}.{m.filter_column} = {_quote(m.filter_value)}; "
        f"grain: one {m.table} row ({m.grain})"
    )
    return f"{line}. {m.description}" if m.description else line


def prompt_lines(measures: Iterable[Measure], tables: Iterable[str]) -> list[str]:
    """Definitions for the measures whose table is in play. Selected by table only."""
    in_play = {str(t).lower() for t in tables}
    return [definition(m) for m in measures if m.table.lower() in in_play]


# ── the structural check ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Violation:
    kind: str
    measure: Measure

    def reason(self) -> str:
        m = self.measure
        target = f"{m.table}.{m.filter_column} = {_quote(m.filter_value)}"
        head = f"{self.kind}:{m.name}: "
        if self.kind == INVERTED:
            return (
                head + f"the SQL excludes {target}, the only rows the declared measure "
                f"{m.key} ({m.expression}) counts"
            )
        cols = ", ".join(f"{m.table}.{c}" for c in m.columns)
        declared = ", ".join(_quote(v) for v in m.domain)
        if self.kind == UNDECLARED:
            return (
                head + f"an aggregate over {cols} pins {m.table}.{m.filter_column} to a "
                f"value outside its declared values ({declared}); the lake stores only "
                f"those, so that filter counts no rows. The declared measure {m.key} "
                f"({m.expression}) counts rows where {target}"
            )
        return (
            head + f"an aggregate over {cols} is the declared measure {m.key} "
            f"({m.expression}), which counts only rows where {target}; add that filter, "
            f"or pin {m.table}.{m.filter_column} to the declared values you mean "
            f"({declared})"
        )


@dataclass(frozen=True, slots=True)
class _Ref:
    base: Scope
    alias: str
    table: str
    column: str
    path: tuple[Scope, ...]

    @property
    def key(self) -> tuple[int, str]:
        return (id(self.base), self.alias)


def _own_nodes(node: exp.Expression) -> Iterator[exp.Expression]:
    """``node`` and its descendants in the same query (nested queries pruned)."""
    for cur in node.walk(
        prune=lambda n: n is not node and isinstance(n, (exp.Select, exp.Subquery))
    ):
        if cur is node or not isinstance(cur, (exp.Select, exp.Subquery)):
            yield cur


def _columns(node: exp.Expression) -> list[exp.Column]:
    return [
        c
        for c in _own_nodes(node)
        if isinstance(c, exp.Column) and not isinstance(c.this, exp.Star)
    ]


class _Checker:
    def __init__(self, schema: Mapping[str, Mapping[str, Any]] | None) -> None:
        self.schema = {
            str(t).lower(): {str(c).lower() for c in cols} for t, cols in (schema or {}).items()
        }

    # column lineage ---------------------------------------------------------

    def _has(self, source: Any, name: str) -> bool:
        if isinstance(source, exp.Table):
            cols = self.schema.get(source.name.lower())
            return cols is None or name in cols
        if isinstance(source, Scope):
            return bool(self._projections(source, name))
        return False

    def _projections(self, scope: Scope, name: str) -> list[tuple[Scope, exp.Expression]]:
        expr = scope.expression
        if isinstance(expr, exp.SetOperation):
            leaves = self._leaves(scope)
            if not leaves:
                return []
            first = leaves[0].expression.selects
            idx = next((i for i, s in enumerate(first) if s.alias_or_name.lower() == name), None)
            if idx is None:
                return []
            return [
                (leaf, leaf.expression.selects[idx])
                for leaf in leaves
                if idx < len(leaf.expression.selects)
            ]
        if not isinstance(expr, exp.Select):
            return []
        out: list[tuple[Scope, exp.Expression]] = []
        for sel in expr.selects:
            if isinstance(sel, exp.Star):
                out.append((scope, exp.column(name)))
            elif isinstance(sel, exp.Column) and isinstance(sel.this, exp.Star):
                out.append((scope, exp.column(name, table=sel.table)))
            elif sel.alias_or_name.lower() == name:
                out.append((scope, sel))
        return out

    def _leaves(self, scope: Scope) -> list[Scope]:
        if not isinstance(scope.expression, exp.SetOperation):
            return [scope]
        out: list[Scope] = []
        for branch in getattr(scope, "set_operation_scopes", None) or []:
            out.extend(self._leaves(branch))
        return out

    def resolve(self, scope: Scope, col: exp.Column, depth: int = 0) -> list[_Ref]:
        if depth > 32:
            return []
        name = col.name.lower()
        selected = scope.selected_sources
        alias = col.table.lower()
        if not alias:
            hits = [a for a, (_, src) in selected.items() if self._has(src, name)]
            if len(hits) != 1:
                return []
            alias = hits[0]
        entry = selected.get(alias)
        if entry is None:
            return []
        source = entry[1]
        if isinstance(source, exp.Table):
            return [_Ref(scope, alias, source.name.lower(), name, (scope,))]
        if not isinstance(source, Scope):
            return []
        out: list[_Ref] = []
        for child, proj in self._projections(source, name):
            for inner in _columns(proj):
                for ref in self.resolve(child, inner, depth + 1):
                    out.append(_Ref(ref.base, ref.alias, ref.table, ref.column, (scope, *ref.path)))
        return out

    # pins ---------------------------------------------------------------------

    def _pin(
        self, scope: Scope, pred: exp.Expression, target: _Ref, column: str
    ) -> tuple[bool, tuple[str, ...]] | None:
        """(positive, values) when ``pred`` compares the target filter column with literals."""
        negate = False
        node = pred.unnest() if isinstance(pred, exp.Paren) else pred
        if isinstance(node, exp.Not):
            negate = True
            node = node.this.unnest() if isinstance(node.this, exp.Paren) else node.this
        col: exp.Expression | None = None
        values: list[exp.Expression] = []
        positive = True
        if isinstance(node, (exp.EQ, exp.NEQ)):
            positive = isinstance(node, exp.EQ)
            left, right = node.left, node.right
            col, values = (left, [right]) if isinstance(left, exp.Column) else (right, [left])
        elif isinstance(node, exp.In) and not node.args.get("query"):
            col, values = node.this, list(node.expressions)
        if not isinstance(col, exp.Column) or not values:
            return None
        if not all(isinstance(v, exp.Literal) and v.is_string for v in values):
            return None
        refs = self.resolve(scope, col)
        if not any(r.key == target.key and r.column == column for r in refs):
            return None
        return (positive != negate, tuple(str(v.this) for v in values))

    @staticmethod
    def _conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
        if node is None:
            return []
        if isinstance(node, exp.Where):
            node = node.this
        node = node.unnest() if isinstance(node, exp.Paren) else node
        if isinstance(node, exp.And):
            return [c.unnest() if isinstance(c, exp.Paren) else c for c in node.flatten()]
        return [node]

    def _scope_preds(self, scope: Scope) -> list[exp.Expression]:
        expr = scope.expression
        return self._conjuncts(expr.args.get("where")) if isinstance(expr, exp.Select) else []

    def _grouped_by(self, scope: Scope, agg: exp.Expression, target: _Ref, column: str) -> bool:
        """True when the aggregate yields one value per filter value (GROUP BY / PARTITION BY)."""
        keys: list[exp.Expression] = []
        group = (
            scope.expression.args.get("group") if isinstance(scope.expression, exp.Select) else None
        )
        if group is not None:
            keys.extend(group.expressions)
        window = agg.parent.parent if isinstance(agg.parent, exp.Filter) else agg.parent
        if isinstance(window, exp.Window):
            keys.extend(window.args.get("partition_by") or [])
        for key in keys:
            key = key.unnest() if isinstance(key, exp.Paren) else key
            if isinstance(key, exp.Column) and any(
                r.key == target.key and r.column == column for r in self.resolve(scope, key)
            ):
                return True
        return False

    def _join_preds(self, scope: Scope) -> list[tuple[exp.Expression, str | None]]:
        """ON conjuncts that filter rows: inner joins (any alias), left joins (joined alias)."""
        expr = scope.expression
        out: list[tuple[exp.Expression, str | None]] = []
        if not isinstance(expr, exp.Select):
            return out
        for join in expr.args.get("joins") or []:
            on = join.args.get("on")
            if on is None:
                continue
            side = (join.side or "").upper()
            kind = (join.kind or "").upper()
            if kind == "CROSS" or side in ("RIGHT", "FULL"):
                continue
            only = join.this.alias_or_name.lower() if side == "LEFT" else None
            out.extend((c, only) for c in self._conjuncts(on))
        return out

    def _agg_preds(self, agg: exp.Expression) -> list[exp.Expression]:
        """Conditions inside the aggregate: FILTER (WHERE ...), CASE WHEN, IF."""
        preds: list[exp.Expression] = []
        parent = agg.parent
        if isinstance(parent, exp.Filter):
            preds.extend(self._conjuncts(parent.expression))
        for node in _own_nodes(agg):
            if isinstance(node, exp.Case):
                subject = node.this
                for branch in node.args.get("ifs") or []:
                    if subject is not None:
                        preds.append(exp.EQ(this=subject.copy(), expression=branch.this.copy()))
                    else:
                        preds.extend(self._conjuncts(branch.this))
            elif isinstance(node, exp.If) and not isinstance(node.parent, exp.Case):
                preds.extend(self._conjuncts(node.this))
        return preds

    def verdict(self, scope: Scope, agg: exp.Expression, m: Measure, ref: _Ref) -> Violation | None:
        pins: list[tuple[bool, tuple[str, ...]]] = []

        def add(sc: Scope, pred: exp.Expression) -> None:
            got = self._pin(sc, pred, ref, m.filter_column)
            if got is not None:
                pins.append(got)

        for pred in self._agg_preds(agg):
            add(scope, pred)  # columns resolve by name and qualifier in the aggregate's scope
        for sc in ref.path:
            for pred in self._scope_preds(sc):
                add(sc, pred)
            for pred, only in self._join_preds(sc):
                cols = [c.table.lower() for c in _columns(pred)]
                if only is not None and any(t and t != only for t in cols):
                    continue
                if only is not None and not all(cols):
                    continue
                add(sc, pred)

        # A pin the declaration cannot hold (exact text, no case folding) sums no
        # rows on a lake that stores the declared values: never a choice to keep.
        if m.domain and any(v not in m.domain for pos, vals in pins if pos for v in vals):
            return Violation(UNDECLARED, m)
        if any(pos for pos, _ in pins) or self._grouped_by(scope, agg, ref, m.filter_column):
            return None
        if any(m.filter_value in vals for pos, vals in pins if not pos):
            return Violation(INVERTED, m)
        return Violation(MISSING, m)

    # measure aggregates ---------------------------------------------------------

    def instances(
        self, scope: Scope, agg: exp.Expression, measures: Sequence[Measure]
    ) -> list[tuple[Measure, _Ref]]:
        groups: dict[tuple[int, str], tuple[_Ref, set[str]]] = {}
        for col in _columns(agg):
            for ref in self.resolve(scope, col):
                groups.setdefault(ref.key, (ref, set()))[1].add(ref.column)
        out: list[tuple[Measure, _Ref]] = []
        ranked = sorted(measures, key=lambda m: -len(m.columns))
        for ref, cols in groups.values():
            for m in ranked:
                if not isinstance(agg, m.aggregates):
                    continue
                if ref.table == m.table.lower() and {c.lower() for c in m.columns} <= cols:
                    out.append((m, ref))
                    break  # the most specific measure names the aggregate
        return out


def _scan(
    sql: str, measures: Sequence[Measure], schema: Mapping[str, Mapping[str, Any]] | None
) -> Iterator[tuple[_Checker, Scope, exp.Expression, Measure, _Ref]]:
    if not sql or not measures:
        return
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return  # the static validator in front refuses unparseable SQL
    if tree is None:
        return
    tables = {t.name.lower() for t in tree.find_all(exp.Table)}
    if not tables & {m.table.lower() for m in measures}:
        return
    try:
        tree = qualify(
            tree,
            schema={t: dict(c) for t, c in (schema or {}).items()},
            dialect="duckdb",
            validate_qualify_columns=False,
            quote_identifiers=False,
        )
    except Exception:  # noqa: BLE001 - unqualified columns still resolve where unambiguous
        tree = sqlglot.parse_one(sql, read="duckdb")
    checker = _Checker(schema)
    agg_types = tuple(dict.fromkeys(a for m in measures for a in m.aggregates))
    for scope in traverse_scope(tree):
        for agg in scope.find_all(*agg_types):
            for m, ref in checker.instances(scope, agg, measures):
                yield checker, scope, agg, m, ref


def check_sql(
    sql: str,
    measures: Sequence[Measure],
    schema: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[Violation]:
    """Violations of declared measure filters in ``sql`` (empty when it keeps them all)."""
    out: list[Violation] = []
    seen: set[tuple[str, str]] = set()
    for checker, scope, agg, m, ref in _scan(sql, measures, schema):
        v = checker.verdict(scope, agg, m, ref)
        if v is None:
            continue
        key = (v.kind, v.measure.key)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def measures_aggregated(
    sql: str,
    measures: Sequence[Measure],
    schema: Mapping[str, Mapping[str, Any]] | None = None,
) -> set[str]:
    """Names of the declared measures ``sql`` aggregates (diagnostics and tests)."""
    return {m.name for _, _, _, m, _ in _scan(sql, measures, schema)}
