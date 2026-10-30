"""PICK-READING-01: a plain-language statement of one gated SQL, with no value in it.

When the vote splits, the reconfirm may offer up to three readings (founder
ruling 2026-10-29, CLAUDE.md section 9). Each is shown as a plain-language
description with a row count and **no values**. This module writes that
description from the gated SQL's syntax tree. It never sees rows or the
question, and the ``plan-sql-freeroute-only`` import contract keeps duckdb and
``CortexOS.execution`` out of it, so it cannot read data.

Order, on a copy of the parsed tree, before anything is rendered:

1. Strip every comment. sqlglot keeps a model's ``/* ... */`` and ``-- ...``
   comments on parsed nodes and prints them, and the gate passes them through,
   so a comment is a value channel (amendment r3). Every render below also
   passes ``comments=False``.
2. Audit every node's attributes (PICK-READING-01 review: a statement said
   "matches" for ``NOT LIKE`` and "matched to" for an ``ANTI JOIN`` because it
   rendered by node type and never read ``negate`` or the join's kind). Each
   node class the renderer reads is declared in :data:`_HANDLED` with exactly
   the args it understands and, per arg, the values it renders. Any node of a
   declared class (or a subclass of one) that carries another arg or value
   raises before anything is rendered: an unknown attribute fails closed, it
   is never dropped. A picked reading is served under its statement, so a
   statement that drops ``NOT`` is a false claim on a served answer. The
   simple renderer reads every node, so every node is audited; the fallback
   reads only its output names, table names and the aliases that bind them,
   its set operations and its top-level LIMIT and OFFSET, so exactly those are
   (:func:`_audit_fallback`). A fallback output is named by a granted column
   only when it reads that column unchanged through every nested query.
3. Resolve positional ``GROUP BY 1`` / ``ORDER BY 2`` to the projection they
   name, so an ordinal is not shown as a value. Out of range raises, and so
   does any ordinal or projection-alias reference in a SELECT whose list
   expands a star (``SELECT *, b ... ORDER BY 2``): which output it names
   depends on the table's columns, which the statement cannot see.
4. Redact every value leaf in the whole tree (every clause, subqueries and
   CTEs included) to :data:`MARK`: ``exp.Literal``, ``exp.Boolean`` and the
   string forms that are not ``exp.Literal`` subclasses. A minus directly over
   a value goes with it. The one exemption is the count of a ``LIMIT``, shown
   as "first N rows"; a leaf ``keep_literal`` accepts is also kept.
5. Name only what the request granted. A model-written alias (of a column,
   a table, a CTE or a subquery) is free text that executes fine, so it is a
   value channel exactly like a literal: ``CASE ... END AS "heavy or light"``
   would print the CASE labels. No model-chosen identifier reaches a statement
   unless it names a granted table or column of the request payload
   (``granted``), and then it is printed in the payload's own spelling, never
   the model's. A computed output is named by its position
   (``computed value 2``), a reference to a projection alias is shown as what
   that projection is, a table alias is shown as its granted table, and a CTE
   or subquery alias is never printed. Any other name raises. A bare name
   that is both a granted column and a projection's alias is bound as DuckDB
   binds it: ORDER BY and HAVING take the alias first, WHERE and GROUP BY the
   column. With ``SUM(quantity_kg) AS quantity_kg``, ``HAVING quantity_kg >
   ...`` filters on the total and is described so; where the binding is not
   known (two outputs share the name, ``GROUP BY ALL`` under HAVING, QUALIFY,
   DISTINCT ON) the statement raises.
6. Fail closed: every remaining leaf must be a kind this module knows. Any
   other leaf (a JSON path part, a placeholder, a node a later sqlglot adds,
   an identifier step 5 did not approve) or a comment that survived step 1
   raises :class:`DescribeError`; the caller then offers nothing.

``keep_literal`` is display redaction only: it decides whether a literal that
is already in the gated SQL is printed or shown as :data:`MARK`. It never
writes, picks, gates or routes SQL.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import cast

import sqlglot
from sqlglot import exp

from CortexOS.identifier_fold import fold_identifier

#: What every redacted value is shown as.
MARK = "<a value>"
#: How a computed output is named: by its position, never by the model's alias.
COMPUTED = "computed value"
#: Longest statement before the shortened marker is appended.
STATEMENT_CAP = 300
SHORTENED = " (shortened)"
_FALLBACK_CHARS = 60
_DIALECT = "duckdb"

# Value leaves. Looked up by name so an older sqlglot that lacks one still
# imports; the fail-closed leaf check covers whatever is missing.
_VALUE_TYPES: tuple[type[exp.Expression], ...] = tuple(
    cls
    for cls in (
        getattr(exp, name, None)
        for name in (
            "Literal",
            "Boolean",
            "ByteString",
            "RawString",
            "HexString",
            "BitString",
            "National",
            "UnicodeString",
        )
    )
    if isinstance(cls, type)
)

# The date parts an ``INTERVAL`` unit or an ``EXTRACT`` part may name. A unit is
# printed, so it is a fixed word or the statement is refused.
_UNITS = frozenset(
    {
        *(
            f"{unit}{plural}"
            for unit in (
                "year",
                "month",
                "week",
                "day",
                "hour",
                "minute",
                "second",
                "millisecond",
                "microsecond",
                "nanosecond",
                "quarter",
                "decade",
            )
            for plural in ("", "s")
        ),
        "century",
        "centuries",
        "millennium",
        "millennia",
        "dow",
        "doy",
        "isodow",
        "isoyear",
        "yearweek",
        "epoch",
        "era",
        "julian",
        "dayofweek",
        "dayofmonth",
        "dayofyear",
        "weekday",
        "weekofyear",
        "timezone",
        "timezone_hour",
        "timezone_minute",
    }
)

_AGGREGATES: dict[type[exp.Expression], str] = {
    exp.Sum: "total of",
    exp.Avg: "average of",
    exp.Min: "smallest",
    exp.Max: "largest",
}

# Each comparison: its words, and its words when the node carries ``negate``
# (``NOT LIKE``, ``NOT ILIKE``, ``!~~``, ``!~~*``). Only Like and ILike declare
# ``negate`` in :data:`_HANDLED`; a negated node of any other class refuses.
_COMPARISONS: dict[type[exp.Expression], tuple[str, str]] = {
    exp.EQ: ("is", ""),
    exp.NEQ: ("is not", ""),
    exp.GT: ("is more than", ""),
    exp.GTE: ("is at least", ""),
    exp.LT: ("is less than", ""),
    exp.LTE: ("is at most", ""),
    exp.Like: ("matches", "does not match"),
    exp.ILike: ("matches, ignoring case,", "does not match, ignoring case,"),
}

# Every fixed phrase the renderer writes. VOCABULARY is their words.
_PHRASES = (
    "Returns",
    "number of rows",
    "number of distinct",
    "number of non-empty",
    "total of",
    "average of",
    "smallest",
    "largest",
    "a computed value",
    "computed value",
    "all columns",
    "all columns of",
    "all columns of a nested query",
    "for each",
    "and",
    "or",
    "not",
    "from",
    "matched to",
    "paired with every row of",
    "keeping rows of",
    "with no match",
    "that have a match in",
    "that have no match in",
    "on",
    "where",
    "having",
    "is",
    "is not",
    "is more than",
    "is at least",
    "is less than",
    "is at most",
    "matches, ignoring case,",
    "does not match",
    "does not match, ignoring case,",
    "is one of",
    "is between",
    "is empty",
    "is not empty",
    "sorted by",
    "descending",
    "with empty values first",
    "first",
    "row",
    "rows",
    "skipping",
    "distinct",
    "computed with",
    "nested query",
    "nested queries",
    "window functions",
    "combining",
    "query results",
    "matching columns by name",
    "keeping distinct rows of the first query result that are not in the second",
    "keeping distinct rows found in both query results",
    "paired by position with",
    "padding the shorter one with empty values",
    "shortened",
)
VOCABULARY: frozenset[str] = frozenset(
    word.strip(",()").lower() for phrase in _PHRASES for word in phrase.split() if word.strip(",()")
)


class DescribeError(ValueError):
    """The SQL cannot be described without risking a value: offer nothing."""


#: Granted table name -> its column names, as the request payload spells them.
Granted = Mapping[str, Iterable[str]]


class _Catalog:
    """The granted tables and columns of the request payload, keyed by DuckDB fold."""

    def __init__(self, granted: Granted) -> None:
        self.tables: dict[str, str] = {}
        self.columns: dict[str, dict[str, str]] = {}
        for table, columns in granted.items():
            name = str(table)
            key = fold_identifier(name)
            if not name or key in self.tables:
                continue
            self.tables[key] = name
            pool = self.columns.setdefault(key, {})
            for column in columns:
                text = str(column)
                if text:
                    pool.setdefault(fold_identifier(text), text)

    def columns_of(self, keys: Iterable[str]) -> dict[str, str]:
        merged: dict[str, str] = {}
        for key in keys:
            for folded, text in self.columns.get(key, {}).items():
                merged.setdefault(folded, text)
        return merged


def cap_statement(text: str) -> str:
    """``text`` cut to :data:`STATEMENT_CAP` characters plus the shortened marker.

    At most ``STATEMENT_CAP + len(SHORTENED)`` (312) characters.
    """
    if len(text) <= STATEMENT_CAP:
        return text
    return text[:STATEMENT_CAP] + SHORTENED


def statement(sql: str, *, keep_literal: Callable[[str], bool], granted: Granted) -> str:
    """A plain-language statement of ``sql`` in which no redacted value appears.

    ``granted`` is the request payload's granted tables, each with its column
    names: the only names a statement may print. Raises :class:`DescribeError`
    on a parse failure, an attribute the statement would not state (step 2),
    an out-of-range ordinal or one over a star, a name that is not granted,
    an unknown leaf or a comment that survived stripping.
    """
    try:
        tree = cast("exp.Expression | None", sqlglot.parse_one(sql, read=_DIALECT))
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal
        raise DescribeError(f"unparsable: {type(exc).__name__}") from None
    if tree is None:
        raise DescribeError("empty statement")
    catalog = _Catalog(granted)
    _strip_comments(tree)
    simple = isinstance(tree, exp.Select) and _simple(tree)
    if simple:
        _audit(tree)
    else:
        _audit_fallback(tree)
    ordinals = _resolve_ordinals(tree)
    kept = _redact(tree, keep_literal)
    if isinstance(tree, exp.Select) and simple:
        approved = _name_simple(tree, catalog, ordinals)
        _leaf_check(tree, kept, approved)
        return cap_statement(_render_select(tree))
    # The fallback prints no tree text: only names it takes from the catalog.
    _leaf_check(tree, kept, None)
    return cap_statement(_render_fallback(tree, catalog))


def _walk(tree: exp.Expression) -> list[exp.Expression]:
    """Every node of ``tree``, materialized so the tree can be edited while looping."""
    return [cast(exp.Expression, node) for node in tree.walk()]


# -- step 1: comments ---------------------------------------------------------------


def _strip_comments(tree: exp.Expression) -> None:
    for node in _walk(tree):
        node.comments = None


# -- step 2: attribute audit ---------------------------------------------------------

#: Says whether one arg value is one the renderer states exactly.
Allowed = Callable[[object], bool]


def _any(_value: object) -> bool:
    """Every value of this arg is rendered (or routes to the fallback, which marks it)."""
    return True


def _one_of(*words: str) -> Allowed:
    """A keyword arg (a join's kind or side): only these keywords are rendered."""
    accepted = frozenset(words)

    def check(value: object) -> bool:
        return isinstance(value, str) and value.upper() in accepted

    return check


def _instance(*kinds: type[exp.Expression]) -> Allowed:
    def check(value: object) -> bool:
        return isinstance(value, kinds)

    return check


def _grouping_keys(value: object) -> bool:
    """Plain GROUP BY keys only: ROLLUP, CUBE and GROUPING SETS add subtotal rows
    that "for each" does not say, so they are not rendered."""
    return isinstance(value, list) and not any(
        isinstance(item, (exp.Rollup, exp.Cube, exp.GroupingSets)) for item in value
    )


_THIS: dict[str, Allowed] = {"this": _any}
_BINARY: dict[str, Allowed] = {"this": _any, "expression": _any}

#: Every node class the renderer reads, with exactly the args it understands
#: and, per arg, the values it states. An arg not listed, or a value its check
#: rejects, raises :class:`DescribeError` before anything is rendered (step 2).
#: Classes not listed are never read for meaning: they are printed as their own
#: SQL (``_sql``), summarized by the fallback's markers, or checked as leaves.
#: An empty or false arg (``negate=False``) is not carried.
_HANDLED: dict[type[exp.Expression], dict[str, Allowed]] = {
    exp.Select: {
        "expressions": _any,
        "distinct": _instance(exp.Distinct),
        "from_": _instance(exp.From),
        "from": _instance(exp.From),
        "joins": _any,
        "where": _instance(exp.Where),
        "group": _instance(exp.Group),
        "having": _instance(exp.Having),
        "order": _instance(exp.Order),
        # FETCH FIRST is a different node: it is not stated, so it is not accepted.
        "limit": _instance(exp.Limit),
        "offset": _instance(exp.Offset),
    },
    exp.From: _THIS,
    # Kind, side and method are rendered per shape by :func:`_join_shape`, which
    # the audit also runs: inner, comma/CROSS, LEFT and POSITIONAL (the only
    # join), SEMI, ANTI.
    exp.Join: {
        "this": _any,
        "on": _any,
        "using": _any,
        "kind": _one_of("INNER", "OUTER", "CROSS", "SEMI", "ANTI"),
        "side": _one_of("LEFT"),
        "method": _one_of("POSITIONAL"),
    },
    # A table is named, nothing else: no schema, catalog, sample, time travel.
    exp.Table: {"this": _any, "alias": _any},
    # An alias that renames a relation's columns would put a name on other data.
    exp.TableAlias: _THIS,
    exp.Alias: {"this": _any, "alias": _any},
    exp.Column: {"this": _any, "table": _any},
    # "all columns": no EXCLUDE, REPLACE, RENAME or ILIKE filter on the star.
    exp.Star: {},
    exp.Distinct: {"expressions": _any},
    exp.Where: _THIS,
    exp.Having: _THIS,
    exp.Group: {"expressions": _grouping_keys},
    exp.Order: {"expressions": _any},
    exp.Ordered: {"this": _any, "desc": _any, "nulls_first": _any},
    exp.Limit: {"expression": _any},
    exp.Offset: {"expression": _any},
    exp.Count: {"this": _any, "big_int": _any},
    exp.Sum: _THIS,
    exp.Avg: _THIS,
    # MIN(x, n) and MAX(x, n) return the n smallest or largest as a list.
    exp.Min: _THIS,
    exp.Max: _THIS,
    exp.Paren: _THIS,
    exp.And: _BINARY,
    exp.Or: _BINARY,
    exp.Not: _THIS,
    exp.Is: _BINARY,
    exp.EQ: _BINARY,
    exp.NEQ: _BINARY,
    exp.GT: _BINARY,
    exp.GTE: _BINARY,
    exp.LT: _BINARY,
    exp.LTE: _BINARY,
    exp.Like: {**_BINARY, "negate": _any},
    exp.ILike: {**_BINARY, "negate": _any},
    exp.In: {"this": _any, "expressions": _any},
    exp.Between: {"this": _any, "low": _any, "high": _any},
    exp.Null: {},
    exp.Var: _THIS,
}
# A subclass of a read class would be read as its parent: it refuses unless listed.
_READ_FAMILIES: tuple[type[exp.Expression], ...] = (*_HANDLED, exp.SetOperation)

_SET_COMMON: dict[str, Allowed] = {
    # A WITH, ORDER BY or nested query of a set operation is summarized by the
    # fallback's markers; its LIMIT and OFFSET are stated.
    "with_": _any,
    "with": _any,
    "this": _any,
    "expression": _any,
    "distinct": _any,
    "order": _instance(exp.Order),
    "limit": _instance(exp.Limit),
    "offset": _instance(exp.Offset),
}
#: The set operations the fallback states, in the words :func:`_set_phrase`
#: picks. Which combinations it states (and that EXCEPT and INTERSECT are
#: DISTINCT, not ALL) is checked there.
_SET_OPERATIONS: dict[type[exp.Expression], dict[str, Allowed]] = {
    exp.Union: {**_SET_COMMON, "by_name": _any},
    exp.Except: _SET_COMMON,
    exp.Intersect: _SET_COMMON,
}


def _carried(value: object) -> bool:
    return value is not None and value is not False and value != [] and value != ""


def _join_shape(join: exp.Join) -> str:
    """The one shape a join is rendered as, or :class:`DescribeError`.

    ``inner``: [INNER] JOIN with ON or USING. ``cross``: a comma or CROSS JOIN,
    no condition. ``left``: LEFT [OUTER] JOIN with a condition, and
    ``positional``: POSITIONAL JOIN, each only as the single join after FROM
    (the rows a LEFT JOIN keeps are the FROM table's, and a later join could
    drop them again). ``semi`` / ``anti``: SEMI or ANTI JOIN with a condition.
    RIGHT, FULL, ASOF, NATURAL, LEFT SEMI/ANTI and any other combination refuse.
    """
    kind = str(join.args.get("kind") or "").upper()
    side = str(join.args.get("side") or "").upper()
    method = str(join.args.get("method") or "").upper()
    condition = _carried(join.args.get("on")) or _carried(join.args.get("using"))
    only = isinstance(join.parent, exp.Select) and join.parent.args.get("joins") == [join]
    if method:
        if method == "POSITIONAL" and not (kind or side or condition) and only:
            return "positional"
    elif not side:
        if kind in {"", "INNER"} and condition:
            return "inner"
        if kind in {"", "CROSS"} and not condition:
            return "cross"
        if kind == "SEMI" and condition:
            return "semi"
        if kind == "ANTI" and condition:
            return "anti"
    elif side == "LEFT" and kind in {"", "OUTER"} and condition and only:
        return "left"
    raise DescribeError("a join the statement does not describe")


#: Node-level checks an arg-by-arg declaration cannot express.
_SHAPES: dict[type[exp.Expression], Callable[[exp.Expression], object]] = {
    exp.Join: lambda node: _join_shape(cast(exp.Join, node)),
}


def _audit_node(
    node: exp.Expression, table: Mapping[type[exp.Expression], Mapping[str, Allowed]]
) -> None:
    """Refuse ``node`` unless its exact class is in ``table`` and every arg it
    carries is declared there with a value its check accepts."""
    declared = table.get(type(node))
    if declared is None:
        raise DescribeError(f"a {type(node).__name__} the statement does not describe")
    for key, value in node.args.items():
        if not _carried(value):
            continue
        allowed = declared.get(key)
        if allowed is None:
            raise DescribeError(f"{type(node).__name__} {key} is not described")
        if not allowed(value):
            raise DescribeError(f"{type(node).__name__} {key} has a value not described")
    shape = _SHAPES.get(type(node))
    if shape is not None:
        shape(node)


def _audit(tree: exp.Expression) -> None:
    """The simple renderer reads every node: audit every node of a read class.

    Runs on the whole parsed tree before ordinals, redaction or naming touch it.
    """
    for node in _walk(tree):
        if isinstance(node, _READ_FAMILIES):
            _audit_node(node, _HANDLED)


def _audit_fallback(tree: exp.Expression) -> None:
    """The fallback reads only some nodes: audit exactly those.

    It states the output names (:func:`_output_selects`), every table name and
    the relation aliases that bind them, the set operations
    (:func:`_set_phrase`) and the top-level LIMIT and OFFSET. Everything else
    (a WHERE, a join, a window) is summarized by "computed with N nested
    queries" or "window functions", never stated, so it is not read here.
    """
    for node in _walk(tree):
        if isinstance(node, (exp.Table, exp.TableAlias)):
            _audit_node(node, _HANDLED)
        elif isinstance(node, exp.SetOperation):
            _audit_node(node, _SET_OPERATIONS)
    _set_phrase(tree)
    for key, kind in (
        ("with_", exp.With),
        ("with", exp.With),
        ("limit", exp.Limit),
        ("offset", exp.Offset),
    ):
        value = tree.args.get(key)
        if _carried(value):
            if not isinstance(value, kind):
                raise DescribeError(f"a {key} the statement does not describe")
            if kind is not exp.With:  # a WITH is counted, not read
                _audit_node(value, _HANDLED)
    for select in _output_selects(tree):
        for projection in select.expressions:
            inner = projection.this if isinstance(projection, exp.Alias) else projection
            for node in (projection, inner, inner.this):
                if isinstance(node, (exp.Alias, exp.Column, exp.Star)):
                    _audit_node(node, _HANDLED)


def _set_phrase(tree: exp.Expression) -> str:
    """The words for the tree's set operations, or ``""``; refuses any it does not state.

    Only UNION [ALL] anywhere (BY NAME all or none): "combining N query
    results". One EXCEPT or INTERSECT (DISTINCT, not ALL) as the whole
    statement: the rows it keeps. Any other mix refuses.
    """
    operations = list(tree.find_all(exp.SetOperation))
    if not operations:
        return ""
    if all(type(op) is exp.Union for op in operations):
        by_name = {bool(op.args.get("by_name")) for op in operations}
        if len(by_name) > 1:
            raise DescribeError("set operations that match columns two ways")
        text = f"combining {len(operations) + 1} query results"
        return text + (", matching columns by name" if True in by_name else "")
    if operations == [tree] and tree.args.get("distinct") is True:
        if type(tree) is exp.Except:
            return "keeping distinct rows of the first query result that are not in the second"
        if type(tree) is exp.Intersect:
            return "keeping distinct rows found in both query results"
    raise DescribeError("a set operation the statement does not describe")


# Projections that expand to as many outputs as a table or value has.
_EXPANDING: tuple[type[exp.Expression], ...] = tuple(
    cls
    for cls in (getattr(exp, name, None) for name in ("Columns", "Explode", "Unnest"))
    if isinstance(cls, type)
)


def _expands(projection: exp.Expression) -> bool:
    """Whether a projection expands to a number of outputs only the table knows."""
    inner = projection.this if isinstance(projection, exp.Alias) else projection
    if isinstance(inner, exp.Star):
        return True
    if isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star):
        return True
    return isinstance(inner, _EXPANDING)


# -- step 3: ordinals ---------------------------------------------------------------


def _ordinal(node: exp.Expression) -> int | None:
    if isinstance(node, exp.Literal) and not node.is_string and node.is_int:
        return int(node.name)
    return None


def _resolve_ordinals(tree: exp.Expression) -> dict[int, int]:
    """Replace each ordinal with a placeholder mark. Returns id(placeholder) -> position.

    The placeholder prints as :data:`MARK` if nothing names it; the simple
    renderer names it by the projection it points at (step 5).
    """
    refs: dict[int, int] = {}
    for select in list(tree.find_all(exp.Select)):
        count = len(select.expressions)
        expands = any(_expands(p) for p in select.expressions)
        items: list[exp.Expression] = []
        group = select.args.get("group")
        if isinstance(group, exp.Group):
            items.extend(group.expressions)
        order = select.args.get("order")
        if isinstance(order, exp.Order):
            items.extend(o.this for o in order.expressions if isinstance(o, exp.Ordered))
        for item in items:
            position = _ordinal(item)
            if position is None:
                continue
            if expands:
                raise DescribeError("an ordinal over a list that expands a star")
            if position < 1 or position > count:
                raise DescribeError(f"ordinal {position} is out of range")
            mark = exp.Var(this=MARK)
            item.replace(mark)
            refs[id(mark)] = position
    return refs


# -- step 4: redaction --------------------------------------------------------------


def _is_value(node: exp.Expression) -> bool:
    return isinstance(node, _VALUE_TYPES)


def _limit_count(node: exp.Expression) -> bool:
    parent = node.parent
    return (
        isinstance(parent, exp.Limit)
        and node.arg_key == "expression"
        and isinstance(node, exp.Literal)
        and not node.is_string
        and node.is_int
    )


def _redact(tree: exp.Expression, keep_literal: Callable[[str], bool]) -> set[int]:
    """Replace every value leaf with :data:`MARK`. Returns ids of the leaves kept."""
    kept: set[int] = set()
    for node in [n for n in _walk(tree) if _is_value(n)]:
        if _limit_count(node):
            kept.add(id(node))
            continue
        text = node.name if not isinstance(node, exp.Boolean) else str(node.this).upper()
        try:
            keep = bool(keep_literal(text))
        except Exception:  # noqa: BLE001 - a predicate that fails keeps nothing
            keep = False
        if keep:
            kept.add(id(node))
            continue
        target = node.parent if isinstance(node.parent, exp.Neg) else node
        target.replace(exp.Var(this=MARK))
    return kept


# -- step 5: names -------------------------------------------------------------------


def _approve(approved: set[int], name: str) -> exp.Identifier:
    ident = exp.to_identifier(name)
    approved.add(id(ident))
    return ident


def _projection_inner(select: exp.Select, position: int) -> exp.Expression:
    projection = select.expressions[position - 1]
    return projection.this if isinstance(projection, exp.Alias) else projection


def _reference(
    select: exp.Select, position: int, pending: dict[int, int], approved: set[int]
) -> exp.Expression:
    """What a reference to projection ``position`` is shown as: never its alias."""
    inner = _projection_inner(select, position)
    if isinstance(inner, exp.Star) or (
        isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star)
    ):
        raise DescribeError("a reference to all columns")
    label = f"{COMPUTED} {position}"
    if not any(id(node) in pending for node in inner.walk()):
        if isinstance(inner, exp.Column):
            label = _column(inner)
        else:
            label = _aggregate(inner) or label
    var = exp.Var(this=label)
    approved.add(id(var))
    return var


def _grouped_names(select: exp.Select, ordinals: dict[int, int]) -> set[str] | None:
    """Folded names of the columns GROUP BY lists. None when it lists anything else.

    An ordinal counts as the projection it names. ``GROUP BY ALL``, ``ROLLUP``,
    ``CUBE``, ``GROUPING SETS`` or an expression give None: how DuckDB binds a
    HAVING name against those is not established here, so it is not guessed.
    """
    group = select.args.get("group")
    if group is None:
        return set()
    if not isinstance(group, exp.Group):
        return None
    if any(value for key, value in group.args.items() if key != "expressions"):
        return None
    names: set[str] = set()
    for item in group.expressions:
        inner = _projection_inner(select, ordinals[id(item)]) if id(item) in ordinals else item
        if not isinstance(inner, exp.Column) or isinstance(inner.this, exp.Star):
            return None
        names.add(fold_identifier(inner.name))
    return names


def _bare_order_key(column: exp.Column) -> bool:
    """Whether ``column`` is a whole ORDER BY key: only parentheses or COLLATE around it."""
    node: exp.Expression = column
    parent = node.parent
    while isinstance(parent, exp.Paren) or (
        isinstance(parent, exp.Collate) and node.arg_key == "this"
    ):
        node, parent = parent, parent.parent
    return isinstance(parent, exp.Ordered) and node.arg_key == "this"


def _binds_to_alias(column: exp.Column, select: exp.Select, grouped: set[str] | None) -> bool:
    """Whether DuckDB binds bare ``column``, also a projection's alias, to that projection.

    DuckDB's rules (checked against DuckDB itself; pinned by
    ``tests/test_plan_sql/test_plan_sql_describe.py``):

    * Inside an aggregate's arguments or its FILTER: the column.
    * SELECT list, WHERE, GROUP BY: the column (an alias only when no column
      has the name). JOIN ON: the column (ON never sees an output's alias).
    * ORDER BY: the alias when the name is the whole key (parentheses and
      COLLATE aside); the column when it is part of a larger expression.
    * HAVING: the column when GROUP BY lists it, otherwise the alias.

    Anything else (a clause not listed, a HAVING over a grouping
    :func:`_grouped_names` does not resolve) raises :class:`DescribeError`.
    """
    clause: str | None = None
    node: exp.Expression = column
    while node.parent is not None and node.parent is not select:
        if isinstance(node.parent, (exp.AggFunc, exp.Filter)):
            return False
        node = cast(exp.Expression, node.parent)
    if node.parent is select:
        clause = node.arg_key
    if clause in {"expressions", "where", "group", "joins"}:
        return False
    if clause == "order":
        return _bare_order_key(column)
    if clause == "having":
        if grouped is None:
            raise DescribeError("a HAVING name whose grouping is not resolved")
        return fold_identifier(column.name) not in grouped
    raise DescribeError("a name whose binding is not known")


def _name_simple(select: exp.Select, catalog: _Catalog, ordinals: dict[int, int]) -> set[int]:
    """Rewrite every name in ``select`` to granted text. Returns the approved node ids.

    A table must be granted; its alias is dropped and a column qualified by
    it is shown qualified by the granted table. A column must be a granted
    column of a table in FROM, printed in the payload's spelling. A bare name
    that is a projection's alias, and an ordinal, is shown as what that
    projection is (:func:`_reference`). Every projection alias is dropped.
    Anything else raises.

    A bare name that is both a granted column and a projection's alias is
    bound the way DuckDB binds it (:func:`_binds_to_alias`), so the statement
    describes what the query computes, not the other reading of the name.
    """
    if select.args.get("qualify") is not None:
        raise DescribeError("a QUALIFY clause the statement does not describe")
    distinct = select.args.get("distinct")
    if isinstance(distinct, exp.Distinct) and distinct.args.get("on") is not None:
        raise DescribeError("a DISTINCT ON the statement does not describe")
    approved: set[int] = set()
    sources: dict[str, str] = {}
    for table in list(select.find_all(exp.Table)):
        if not isinstance(table.this, exp.Identifier):
            raise DescribeError("a source that is not a granted table")
        key = fold_identifier(table.name)
        if key not in catalog.tables:
            raise DescribeError("a table that is not granted")
        alias = table.args.get("alias")
        if isinstance(alias, exp.TableAlias) and alias.columns:
            raise DescribeError("a table alias that renames columns")
        sources[fold_identifier(table.alias_or_name)] = key
        table.set("this", _approve(approved, catalog.tables[key]))
        for part in ("alias", "db", "catalog"):
            table.set(part, None)
    local = catalog.columns_of(sources.values())
    aliases: dict[str, list[int]] = {}
    for position, projection in enumerate(select.expressions, 1):
        if isinstance(projection, exp.Alias) and projection.alias:
            aliases.setdefault(fold_identifier(projection.alias), []).append(position)
    grouped = _grouped_names(select, ordinals)

    for join in select.args.get("joins") or []:
        using = join.args.get("using") or []
        named = []
        for item in using:
            name = item.name if isinstance(item, (exp.Identifier, exp.Column)) else ""
            if not name or fold_identifier(name) not in local:
                raise DescribeError("a USING name that is not a granted column")
            named.append(_approve(approved, local[fold_identifier(name)]))
        if using:
            join.set("using", named)

    pending: dict[int, int] = dict(ordinals)
    for column in list(select.find_all(exp.Column)):
        if column.args.get("db") or column.args.get("catalog"):
            raise DescribeError("a qualified name that is not a granted column")
        owner: str | None = None
        if column.table:
            owner = sources.get(fold_identifier(column.table))
            if owner is None:
                raise DescribeError("a qualifier that is not a granted table")
            column.set("table", _approve(approved, catalog.tables[owner]))
        if isinstance(column.this, exp.Star):
            continue
        pool = catalog.columns.get(owner, {}) if owner else local
        folded = fold_identifier(column.name)
        positions = aliases.get(folded, []) if owner is None else []
        if positions and (folded not in pool or _binds_to_alias(column, select, grouped)):
            if len(positions) > 1:
                # DuckDB takes one of the outputs that share the name; say none.
                raise DescribeError("a name that more than one output is called")
            pending[id(column)] = positions[0]
        elif folded in pool:
            column.set("this", _approve(approved, pool[folded]))
        else:
            raise DescribeError("a name that is not a granted column")

    references = [node for node in select.walk() if id(node) in pending]
    if references and any(_expands(p) for p in select.expressions):
        # Which output a name or position means depends on the star's columns.
        raise DescribeError("a reference to an output of a list that expands a star")
    for node in references:
        node.replace(_reference(select, pending[id(node)], pending, approved))
    for projection in list(select.expressions):
        if isinstance(projection, exp.Alias):
            projection.replace(projection.this)
    return approved


# -- step 6: fail-closed leaf check -------------------------------------------------


def _children(node: exp.Expression) -> list[exp.Expression]:
    return list(node.iter_expressions())


def _allowed_leaf(node: exp.Expression, kept: set[int], approved: set[int] | None) -> bool:
    """Whether a leaf may stay. ``approved`` is None for the fallback, which prints no tree text."""
    if isinstance(node, exp.Identifier):
        return approved is None or id(node) in approved
    if isinstance(node, exp.DataType):
        return approved is None or node.this != exp.DataType.Type.USERDEFINED
    if isinstance(node, (exp.Star, exp.Null, exp.Distinct)):
        return True
    if isinstance(node, exp.Var):
        if node.name == MARK or (approved is not None and id(node) in approved):
            return True
        if node.arg_key == "unit" or isinstance(node.parent, exp.Extract):
            return approved is None or node.name.lower() in _UNITS
        return False
    if _is_value(node):
        return id(node) in kept
    if isinstance(node, exp.Anonymous):
        # A function sqlglot does not know prints its name as written.
        return approved is None
    if isinstance(node, exp.Func):
        return not _children(node)
    return False


def _leaf_check(tree: exp.Expression, kept: set[int], approved: set[int] | None) -> None:
    for node in _walk(tree):
        if node.comments:
            raise DescribeError("a comment survived stripping")
        if approved is not None and isinstance(node, exp.Anonymous):
            raise DescribeError("a function name that is not a known function")
        if _children(node):
            continue
        if not _allowed_leaf(node, kept, approved):
            raise DescribeError(f"unknown leaf {type(node).__name__}")


# -- rendering ----------------------------------------------------------------------


def _sql(node: exp.Expression) -> str:
    """The sanitized subtree's SQL, cut to 60 characters without splitting a mark."""
    text = node.sql(dialect=_DIALECT, comments=False)
    if len(text) <= _FALLBACK_CHARS:
        return text
    cut = _FALLBACK_CHARS
    start = text.rfind(MARK[0], 0, cut)
    if start != -1 and text.startswith(MARK, start) and start + len(MARK) > cut:
        cut = start
    return text[:cut].rstrip() + "..."


def _aggregate(node: exp.Expression) -> str:
    """The words for an aggregate call, or ``""`` when ``node`` is not one."""
    if isinstance(node, exp.Count):
        arg = node.this
        if arg is None or isinstance(arg, exp.Star):
            return "number of rows"
        if isinstance(arg, exp.Distinct):
            return f"number of distinct {_join_words(_operand(e) for e in arg.expressions)}"
        return f"number of non-empty {_operand(arg)}"
    for kind, word in _AGGREGATES.items():
        if type(node) is kind:
            return f"{word} {_operand(node.this)}"
    return ""


def _column(node: exp.Column) -> str:
    """A column already rewritten to granted names by :func:`_name_simple`."""
    table = node.table
    return f"{table}.{node.name}" if table else node.name


def _operand(node: exp.Expression | None) -> str:
    if node is None:
        return ""
    if isinstance(node, exp.Paren):
        return _operand(node.this)
    if isinstance(node, exp.Column):
        if isinstance(node.this, exp.Star):
            # ``t.*`` in a join is t's columns only, never "all columns".
            return f"all columns of {node.table}" if node.table else "all columns"
        return _column(node)
    if isinstance(node, exp.Var) and node.name == MARK:
        return MARK
    return _aggregate(node) or _sql(node)


def _join_words(parts: Iterable[str], word: str = "and") -> str:
    items = [p for p in parts if p]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {word} " + items[-1]


def _condition(node: exp.Expression | None) -> str:
    if node is None:
        return ""
    if isinstance(node, exp.Paren):
        inner = node.this
        text = _condition(inner)
        return f"({text})" if isinstance(inner, (exp.And, exp.Or)) else text
    if isinstance(node, exp.And):
        return f"{_condition(node.this)} and {_condition(node.expression)}"
    if isinstance(node, exp.Or):
        return f"{_condition(node.this)} or {_condition(node.expression)}"
    if isinstance(node, exp.Not):
        inner = node.this
        if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
            return f"{_operand(inner.this)} is not empty"
        return f"not ({_condition(inner)})"
    if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
        return f"{_operand(node.this)} is empty"
    for kind, (word, negated) in _COMPARISONS.items():
        if type(node) is kind:
            if node.args.get("negate"):
                if not negated:
                    raise DescribeError("a negation the statement does not describe")
                word = negated
            return f"{_operand(node.this)} {word} {_operand(node.expression)}"
    if isinstance(node, exp.In) and node.expressions:
        values = ", ".join(_operand(v) for v in node.expressions)
        return f"{_operand(node.this)} is one of ({values})"
    if isinstance(node, exp.Between):
        low, high = node.args.get("low"), node.args.get("high")
        return f"{_operand(node.this)} is between {_operand(low)} and {_operand(high)}"
    return _sql(node)


def _projection(node: exp.Expression, position: int) -> str:
    """One output, never under its alias (step 5 dropped every projection alias)."""
    if isinstance(node, exp.Star):
        return "all columns"
    if isinstance(node, exp.Column):
        return _operand(node)
    words = _aggregate(node)
    if words:
        return words
    if isinstance(node, exp.Var) and node.name != MARK:
        return node.name  # a reference to an earlier projection, already named
    return f"{COMPUTED} {position} ({_sql(node)})"


def _table(node: exp.Expression) -> str:
    if isinstance(node, exp.Table):
        return node.name
    raise DescribeError("a source that is not a granted table")


def _arg(node: exp.Expression, *names: str) -> exp.Expression | None:
    for name in names:
        value = node.args.get(name)
        if isinstance(value, exp.Expression):
            return value
    return None


def _from_text(select: exp.Select) -> str:
    """The FROM table, then each join in the words of its shape (:func:`_join_shape`)."""
    source = _arg(select, "from_", "from")
    first = _table(source.this) if isinstance(source, exp.From) else ""
    text = first
    for join in select.args.get("joins") or []:
        shape = _join_shape(join)
        target = _table(join.this)
        on = _arg(join, "on")
        using = join.args.get("using") or []
        condition = _condition(on) if on is not None else _join_words(_operand(u) for u in using)
        if shape == "cross":
            text += f", paired with every row of {target}"
        elif shape == "positional":
            text += f", paired by position with {target}, padding the shorter one with empty values"
        elif shape == "inner":
            text += f", matched to {target} on {condition}"
        elif shape == "left":
            text += f", matched to {target} on {condition}, keeping rows of {first} with no match"
        elif shape == "semi":
            text += f" that have a match in {target} on {condition}"
        elif shape == "anti":
            text += f" that have no match in {target} on {condition}"
        else:  # pragma: no cover - _join_shape returns only the shapes above
            raise DescribeError("a join the statement does not describe")
    return text


def _named_source(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier)


def _simple(tree: exp.Expression) -> bool:
    """One SELECT over named tables only. Anything else gets the honest fallback.

    A table function or LATERAL source has no granted name to show, so it is
    described by the fallback, which prints no tree text.
    """
    if not isinstance(tree, exp.Select):
        return False
    if tree.args.get("with") or tree.args.get("with_"):
        return False
    source = _arg(tree, "from_", "from")
    if isinstance(source, exp.From) and not _named_source(source.this):
        return False
    if any(not _named_source(join.this) for join in tree.args.get("joins") or []):
        return False
    return not any(
        isinstance(node, (exp.Subquery, exp.CTE, exp.Window, exp.SetOperation, exp.Lateral))
        for node in tree.walk()
        if node is not tree
    )


def _count(node: exp.Expression | None) -> str:
    """A LIMIT or OFFSET count: values and marks only, never a name."""
    if node is None:
        raise DescribeError("a row count with no value")
    for part in node.walk():
        if isinstance(part, (exp.Identifier, exp.Column, exp.Anonymous, exp.Query)):
            raise DescribeError("a row count that names something")
        if isinstance(part, exp.Var) and part.name != MARK:
            raise DescribeError("a row count that names something")
    return _operand(node)


def _limit_text(select: exp.Expression) -> list[str]:
    parts: list[str] = []
    offset = select.args.get("offset")
    if isinstance(offset, exp.Offset):
        parts.append(f"skipping {_count(offset.expression)} rows")
    limit = select.args.get("limit")
    if isinstance(limit, exp.Limit) and limit.expression is not None:
        count = _count(limit.expression)
        parts.append(f"first {count} {'row' if count == '1' else 'rows'}")
    return parts


def _render_select(select: exp.Select) -> str:
    projections = _join_words(
        _projection(p, position) for position, p in enumerate(select.expressions, 1)
    )
    if isinstance(select.args.get("distinct"), exp.Distinct):
        projections = f"distinct {projections}"
    text = f"Returns {projections}"
    group = select.args.get("group")
    if isinstance(group, exp.Group) and group.expressions:
        text += f" for each {_join_words(_operand(g) for g in group.expressions)}"
    source = _from_text(select)
    if source:
        text += f" from {source}"
    where = select.args.get("where")
    if isinstance(where, exp.Where):
        text += f" where {_condition(where.this)}"
    having = select.args.get("having")
    if isinstance(having, exp.Having):
        text += f" having {_condition(having.this)}"
    tail: list[str] = []
    order = select.args.get("order")
    if isinstance(order, exp.Order) and order.expressions:
        keys = []
        for ordered in order.expressions:
            key = _operand(ordered.this) if isinstance(ordered, exp.Ordered) else _operand(ordered)
            if isinstance(ordered, exp.Ordered) and ordered.args.get("desc"):
                key += " descending"
            # DuckDB sorts empty values last in both directions unless told
            # otherwise; NULLS LAST is that default, so only NULLS FIRST is said.
            if isinstance(ordered, exp.Ordered) and ordered.args.get("nulls_first"):
                key += " with empty values first"
            keys.append(key)
        tail.append("sorted by " + ", ".join(keys))
    tail.extend(_limit_text(select))
    if tail:
        text += ", " + ", ".join(tail)
    return text


#: How many CTE or subquery levels an output name is followed through.
_RESOLVE_DEPTH = 8


def _relations(select: exp.Select) -> list[exp.Expression]:
    source = _arg(select, "from_", "from")
    found = [source.this] if isinstance(source, exp.From) else []
    return found + [join.this for join in select.args.get("joins") or []]


def _derived(relation: exp.Expression, ctes: Mapping[str, exp.Expression]) -> exp.Expression | None:
    """The query a relation reads when it is a subquery or a CTE; None for a table."""
    if isinstance(relation, exp.Subquery):
        return relation.this
    if isinstance(relation, exp.Table) and isinstance(relation.this, exp.Identifier):
        if not (relation.args.get("db") or relation.args.get("catalog")):
            return ctes.get(fold_identifier(relation.name))
    return None


def _reads_column(
    select: exp.Select,
    column: exp.Column,
    catalog: _Catalog,
    ctes: Mapping[str, exp.Expression],
    depth: int,
) -> bool:
    """Whether ``column`` in ``select`` reads the granted column of its own name, unchanged.

    Through a subquery or CTE the name must reach a projection that passes the
    column on unrenamed (``SELECT txn_type AS sku`` does not: its ``sku`` is
    txn_type). Anything not followed exactly (several derived sources, a
    computed projection, a deeper nesting than :data:`_RESOLVE_DEPTH`) is no.
    """
    if depth > _RESOLVE_DEPTH:
        return False
    folded = fold_identifier(column.name)
    relations = _relations(select)
    if column.table:
        qualifier = fold_identifier(column.table)
        relations = [r for r in relations if fold_identifier(r.alias_or_name) == qualifier]
        if len(relations) != 1:
            return False
    derived = [_derived(relation, ctes) for relation in relations]
    if all(query is None for query in derived):
        # Granted tables only: bound as the simple path binds a bare name.
        return any(
            isinstance(relation, exp.Table)
            and isinstance(relation.this, exp.Identifier)
            and folded in catalog.columns.get(fold_identifier(relation.name), {})
            for relation in relations
        )
    if len(relations) != 1 or derived[0] is None:
        return False
    return _query_reads(derived[0], folded, catalog, ctes, depth + 1)


def _query_reads(
    query: exp.Expression,
    folded: str,
    catalog: _Catalog,
    ctes: Mapping[str, exp.Expression],
    depth: int,
) -> bool:
    """Whether output ``folded`` of ``query`` is the granted column of that name, unchanged."""
    while isinstance(query, (exp.Subquery, exp.Paren)):
        query = query.this
    if not isinstance(query, exp.Select):
        return False
    named = [
        p
        for p in query.expressions
        if not _expands(p) and fold_identifier(p.alias_or_name) == folded
    ]
    if len(named) > 1:
        return False
    if named:
        inner = named[0].this if isinstance(named[0], exp.Alias) else named[0]
        return (
            isinstance(inner, exp.Column)
            and not isinstance(inner.this, exp.Star)
            and fold_identifier(inner.name) == folded
            and _reads_column(query, inner, catalog, ctes, depth)
        )
    stars = [p for p in query.expressions if _expands(p)]
    if stars and all(isinstance(p, exp.Star) for p in stars):
        return _reads_column(query, exp.column(folded), catalog, ctes, depth)
    return False


def _output_name(
    projection: exp.Expression,
    position: int,
    select: exp.Select,
    catalog: _Catalog,
    ctes: Mapping[str, exp.Expression],
) -> str:
    """A granted column's payload spelling, or the output's position. Never an alias.

    A column is named only when it reads that granted column unchanged
    (:func:`_reads_column`); ``SELECT sku FROM (SELECT txn_type AS sku ...)``
    is ``computed value 1``, never "sku".
    """
    inner = projection.this if isinstance(projection, exp.Alias) else projection
    if isinstance(inner, exp.Star):
        return "all columns"
    if isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star):
        return _star_of(select, inner, catalog, ctes)
    if isinstance(inner, exp.Column):
        folded = fold_identifier(inner.name)
        renamed = isinstance(projection, exp.Alias) and fold_identifier(projection.alias) != folded
        columns = catalog.columns_of(catalog.tables)
        if folded in columns and not renamed and _reads_column(select, inner, catalog, ctes, 0):
            return columns[folded]
    return f"{COMPUTED} {position}"


def _star_of(
    select: exp.Select,
    column: exp.Column,
    catalog: _Catalog,
    ctes: Mapping[str, exp.Expression],
) -> str:
    """``q.*``: the columns of the one relation ``q`` names, by its granted name."""
    if not column.table:
        return "all columns"
    qualifier = fold_identifier(column.table)
    relations = [r for r in _relations(select) if fold_identifier(r.alias_or_name) == qualifier]
    if len(relations) != 1:
        # A struct's fields, or a name no relation has: not stated.
        raise DescribeError("a star whose relation is not known")
    relation = relations[0]
    if _derived(relation, ctes) is not None or not (
        isinstance(relation, exp.Table) and isinstance(relation.this, exp.Identifier)
    ):
        return "all columns of a nested query"
    key = fold_identifier(relation.name)
    if key not in catalog.tables:
        raise DescribeError("a table that is not granted")
    return f"all columns of {catalog.tables[key]}"


def _branches(tree: exp.Expression) -> list[exp.Expression]:
    """The branches of the set operations at the top of ``tree``, in order."""
    if isinstance(tree, exp.SetOperation):
        return _branches(tree.this) + _branches(tree.expression)
    while isinstance(tree, (exp.Subquery, exp.Paren)):
        tree = tree.this
    return [tree]


def _output_selects(tree: exp.Expression) -> list[exp.Select]:
    """The SELECTs whose lists name the outputs: every branch of a set operation
    (each output is read from all of them), or the statement itself."""
    branches = _branches(tree)
    return (
        [b for b in branches if isinstance(b, exp.Select)]
        if all(isinstance(b, exp.Select) for b in branches)
        else []
    )


def _output_names(tree: exp.Expression, catalog: _Catalog) -> str:
    """Each output once. Across UNION branches an output is named only when every
    branch reads the same granted column at its place (by position, or by name
    under BY NAME); otherwise it is a computed value."""
    selects = _output_selects(tree)
    if not selects:
        return "a computed value"
    ctes = {fold_identifier(cte.alias_or_name): cte.this for cte in tree.find_all(exp.CTE)}
    if isinstance(tree, exp.SetOperation) and tree.args.get("by_name"):
        names: list[str] = []
        position = 0
        for select in selects:
            for projection in select.expressions:
                position += 1
                name = _output_name(projection, position, select, catalog, ctes)
                if name not in names:
                    names.append(name)
        return _join_words(names)
    first = selects[0]
    width = len(first.expressions)
    if any(len(select.expressions) != width for select in selects):
        return "a computed value"
    outputs = []
    for position in range(1, width + 1):
        seen = {
            _output_name(select.expressions[position - 1], position, select, catalog, ctes)
            for select in selects
        }
        outputs.append(seen.pop() if len(seen) == 1 else f"{COMPUTED} {position}")
    return _join_words(outputs)


def _fallback_tables(tree: exp.Expression, catalog: _Catalog) -> tuple[list[str], int]:
    """The granted tables the tree reads, in payload spelling, and its CTE count.

    A reference to a CTE is not printed (its name is the model's). A named
    source that is neither a CTE nor a granted table raises.
    """
    ctes = {fold_identifier(cte.alias_or_name) for cte in tree.find_all(exp.CTE)}
    tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue  # a table function: no name of its own to print
        key = fold_identifier(table.name)
        qualified = bool(table.args.get("db") or table.args.get("catalog"))
        if key in ctes and not qualified:
            continue
        if key not in catalog.tables:
            raise DescribeError("a table that is not granted")
        tables.add(catalog.tables[key])
    return sorted(tables), len(ctes)


def _render_fallback(tree: exp.Expression, catalog: _Catalog) -> str:
    tables, cte_count = _fallback_tables(tree, catalog)
    nested = len(list(tree.find_all(exp.Subquery))) + cte_count
    combined = _set_phrase(tree)
    text = f"Returns {_output_names(tree, catalog)}"
    if tables:
        text += f" from {_join_words(tables)}"
    extras: list[str] = []
    if nested:
        extras.append(
            f"computed with {nested} {'nested query' if nested == 1 else 'nested queries'}"
        )
    if combined:
        extras.append(combined)
    if any(True for _ in tree.find_all(exp.Window)):
        extras.append("window functions")
    if extras:
        text += ", " + ", ".join(extras)
    tail = _limit_text(tree)
    if tail:
        text += ", " + ", ".join(tail)
    return text


__all__ = [
    "COMPUTED",
    "MARK",
    "SHORTENED",
    "STATEMENT_CAP",
    "VOCABULARY",
    "DescribeError",
    "Granted",
    "cap_statement",
    "statement",
]
