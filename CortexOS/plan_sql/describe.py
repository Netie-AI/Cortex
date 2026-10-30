"""PICK-READING-01: a plain-language statement of one gated SQL, with no value in it.

When the vote splits, the reconfirm may offer up to three readings (founder
ruling 2026-10-29, CLAUDE.md section 9). Each is shown as a plain-language
description with a row count and **no values**. A picked reading is served
with ``reading: <statement>`` as its first assumption, so a statement that
says anything the SQL does not do is a false claim on a served answer
(WRONG=0). This module writes that statement from the gated SQL's syntax
tree. It never sees rows or the question, and the ``plan-sql-freeroute-only``
import contract keeps duckdb and ``CortexOS.execution`` out of it, so it cannot
read data.

**Core grammar, everything else refused.** Three review rounds found
constructs a "describe almost anything" renderer misdescribed (a comparison
with NULL stated as an IS NULL test, nested star modifiers, self-joins and
nested scopes merged into one table, a parenthesized ordinal shown as a value,
a fallback that dropped WHERE). A reading that cannot be described exactly is
simply not offered; the user still gets the normal reconfirm. So a statement
is written only for this grammar, and :class:`DescribeError` is raised for
anything else:

* One ``SELECT [DISTINCT]`` with a ``FROM``. No CTE, set operation, subquery,
  ``EXISTS``, ``IN (subquery)``, window, ``QUALIFY``, ``OFFSET``, PIVOT /
  UNPIVOT, table function, VALUES, sampling, LATERAL or schema / catalog
  qualification anywhere.
* Sources: one granted table, or ``[INNER] JOIN``, ``SEMI JOIN``, ``ANTI
  JOIN`` and a single ``LEFT [OUTER] JOIN``, each with an ``ON`` that is an
  AND of comparisons or boolean columns. Every granted table at most once in
  the statement (no self-join); every relation name or alias distinct, and
  none equal to another granted table or a column of a table it reads. A SEMI
  or ANTI join's table is read only in its own ON; no WHERE, and no HAVING
  outside an aggregate (directly or through an output's name), reads the table
  of a LEFT JOIN (it would drop the unmatched rows "keeping rows" promises).
* Outputs: a granted column, ``*`` or ``t.*`` with no modifier (and then no
  ordinal or output-name reference), ``COUNT(*)``, ``COUNT(1)``,
  ``COUNT(col)``, ``COUNT(DISTINCT col)``, ``SUM`` / ``AVG`` / ``MIN`` / ``MAX``
  of one granted column, or a searched ``CASE`` whose conditions are in the
  grammar and whose labels are values.
* Conditions: ``AND`` / ``OR`` / ``NOT``, ``=``, ``<>``, ``<``, ``<=``, ``>``,
  ``>=``, ``[NOT] LIKE`` / ``ILIKE``, ``[NOT] IN`` (a list of values),
  ``[NOT] BETWEEN`` two values, ``IS [NOT] NULL`` and a bare column the payload
  types BOOLEAN. A value is a literal, a negative number or one ``CAST`` of a
  value to a built-in type (not a CAST of a CAST); ``NULL`` is never a value
  (only ``IS [NOT] NULL`` names it), and a comparison of two values is
  refused. Aggregates only in HAVING and ORDER BY.
* ``GROUP BY`` granted columns, output names, ordinals (parenthesized ones
  resolved) or a ``CASE`` identical to an output. ``HAVING`` with DuckDB's
  name binding. ``ORDER BY`` granted columns, aggregates, ordinals or output
  names, with ``DESC`` and ``NULLS FIRST`` stated (``NULLS LAST`` is DuckDB's
  default, so it is the unstated case); under ``DISTINCT`` only a key that is
  an output (an ordinal, an output's name, or the SQL text of an output),
  since DuckDB sorts each distinct row by an arbitrary source row's value of
  any other key. ``LIMIT n``, said as ``int(n)``.

Order, on a copy of the parsed tree, before anything is rendered:

1. Strip every comment. sqlglot keeps a model's ``/* ... */`` and ``-- ...``
   comments on parsed nodes and prints them, and the gate passes them through,
   so a comment is a value channel (amendment r3).
2. Audit every node's attributes (PICK-READING-01 review: a statement said
   "matches" for ``NOT LIKE`` and "matched to" for an ``ANTI JOIN`` because it
   rendered by node type and never read ``negate`` or the join's kind). Each
   node class the renderer reads is declared in :data:`_HANDLED` with exactly
   the args it understands and, per arg, the values it renders. Any other arg
   or value raises: an unknown attribute fails closed, it is never dropped.
3. Check the core grammar (:class:`_Grammar`): every node of the tree must be
   accepted at its position, or the statement raises.
4. Resolve ``GROUP BY 1`` / ``ORDER BY (2)`` (and a GROUP BY CASE that repeats
   an output) to the output they name, so an ordinal is not shown as a value.
5. Redact every value leaf to :data:`MARK`. The one exemption is the count of a
   ``LIMIT``, shown as "first N rows"; a leaf ``keep_literal`` accepts is also
   kept.
6. Name only what the request granted. A model-written alias is free text that
   executes fine, so it is a value channel exactly like a literal. No
   model-chosen identifier reaches a statement unless it names a granted table
   or column of the request payload (``granted``), and then it is printed in
   the payload's own spelling. A CASE output is named by its position
   (``computed value 2``); a reference to an output's alias is shown as what
   that output is. The payload may list a subset of a table's columns and
   DuckDB binds against all of them, so an output alias that is not a plain
   passthrough (``sku AS sku``) is accepted only when no other name of the
   statement could be it: no relation or qualifier has its name, and every
   column reference spelled like it is a whole ORDER BY key or a HAVING name,
   where DuckDB takes the alias first (a bare name in WHERE, GROUP BY, ON, the
   SELECT list or an aggregate equal to it refuses, listed or not). A bare
   column must be a granted column of exactly one table its clause reads and
   listed by no other table of the statement; where a binding is not known it
   raises.
7. Fail closed: every remaining leaf must be a kind this module knows, and no
   comment may survive.

``keep_literal`` is display redaction only: it decides whether a literal that
is already in the gated SQL is printed or shown as :data:`MARK`. It never
writes, picks, gates or routes SQL.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import cast

import sqlglot
from sqlglot import exp

from CortexOS.identifier_fold import fold_identifier

#: What every redacted value is shown as.
MARK = "<a value>"
#: How a CASE output is named: by its position, never by the model's alias.
COMPUTED = "computed value"
#: Longest statement before the shortened marker is appended.
STATEMENT_CAP = 300
SHORTENED = " (shortened)"
_DIALECT = "duckdb"

# Value leaves. Looked up by name so an older sqlglot that lacks one still
# imports; the fail-closed checks cover whatever is missing.
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
_ORDER_COMPARISONS: tuple[type[exp.Expression], ...] = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
)

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
    "computed value",
    "all columns",
    "all columns of",
    "for each",
    "and",
    "or",
    "not",
    "from",
    "matched to",
    "keeping rows of",
    "with no match",
    "keeping only rows with some match in",
    "keeping only rows with no match in",
    "on",
    "where",
    "having",
    "is",
    "is not",
    "is more than",
    "is at least",
    "is less than",
    "is at most",
    "matches",
    "matches, ignoring case,",
    "does not match",
    "does not match, ignoring case,",
    "is one of",
    "is between",
    "is empty",
    "is not empty",
    "when",
    "then",
    "otherwise",
    "empty",
    "sorted by",
    "descending",
    "with empty values first",
    "first",
    "row",
    "rows",
    "distinct",
    "shortened",
)
VOCABULARY: frozenset[str] = frozenset(
    word.strip(",()").lower() for phrase in _PHRASES for word in phrase.split() if word.strip(",()")
)
_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A name spelled like a value a statement can show (a kept TRUE) is quoted too.
_VALUE_WORDS = frozenset({"true", "false", "null"})


class DescribeError(ValueError):
    """The SQL cannot be described exactly: offer nothing."""


#: Granted table name -> its column names, as the request payload spells them;
#: or column name -> the payload's type for it (``None`` when the payload gives none).
Granted = Mapping[str, Iterable[str] | Mapping[str, str | None]]
#: Payload types a bare column may have to be read as a condition ("where flag").
_BOOLEAN_TYPES = frozenset({"BOOLEAN", "BOOL", "LOGICAL"})


class _Catalog:
    """The granted tables and columns of the request payload, keyed by DuckDB fold.

    It lists what the request granted, not what the table holds: DuckDB sees
    every column of a table, and the payload may list a subset. So a name the
    catalog does not list may still be a real column (:func:`_check_output_names`).
    """

    def __init__(self, granted: Granted) -> None:
        self.tables: dict[str, str] = {}
        self.columns: dict[str, dict[str, str]] = {}
        self.types: dict[str, dict[str, str]] = {}
        for table, columns in granted.items():
            name = str(table)
            key = fold_identifier(name)
            if not name or key in self.tables:
                continue
            self.tables[key] = name
            pool = self.columns.setdefault(key, {})
            types = self.types.setdefault(key, {})
            typed = columns if isinstance(columns, Mapping) else None
            for column in columns:
                text = str(column)
                if not text:
                    continue
                folded = fold_identifier(text)
                if folded in pool:
                    continue
                pool[folded] = text
                kind = typed.get(column) if typed is not None else None
                types[folded] = str(kind or "").strip().upper()


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
    on a parse failure, anything outside the core grammar, an attribute the
    statement would not state, an out-of-range ordinal or one over a star, a
    name that is not granted, an unknown leaf, a comment that survived
    stripping, or a statement longer than :data:`STATEMENT_CAP`.
    """
    try:
        tree = cast("exp.Expression | None", sqlglot.parse_one(sql, read=_DIALECT))
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal
        raise DescribeError(f"unparsable: {type(exc).__name__}") from None
    if tree is None:
        raise DescribeError("empty statement")
    if type(tree) is not exp.Select:
        raise DescribeError("only a single SELECT is described")
    catalog = _Catalog(granted)
    _strip_comments(tree)
    _audit(tree)
    _Grammar(tree).check()
    ordinals = _resolve_ordinals(tree)
    kept = _redact(tree, keep_literal)
    approved = _name(tree, catalog, ordinals)
    _leaf_check(tree, kept, approved)
    text = _render_select(tree)
    if len(text) > STATEMENT_CAP:
        # A cut statement can end inside a phrase ("is not" cut to "is"): it is
        # not exact, so the reading is not offered.
        raise DescribeError("a statement too long to show whole")
    return text


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
    """Every value of this arg is rendered (the grammar checks its shape)."""
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


def _builtin_type(value: object) -> bool:
    """A CAST target named by DuckDB itself: never a model-chosen type name."""
    return isinstance(value, exp.DataType.Type) and value != exp.DataType.Type.USERDEFINED


_THIS: dict[str, Allowed] = {"this": _any}
_BINARY: dict[str, Allowed] = {"this": _any, "expression": _any}

#: Every node class the renderer reads, with exactly the args it understands
#: and, per arg, the values it states. An arg not listed, or a value its check
#: rejects, raises :class:`DescribeError` before anything is rendered (step 2).
#: Value leaves (``_VALUE_TYPES``) are not read for meaning: they are redacted.
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
    },
    exp.From: _THIS,
    # Kind and side are rendered per shape by :func:`_join_shape`, which the
    # audit also runs: inner, a single LEFT, SEMI, ANTI. Always with ON.
    exp.Join: {
        "this": _any,
        "on": _any,
        "kind": _one_of("INNER", "OUTER", "SEMI", "ANTI"),
        "side": _one_of("LEFT"),
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
    # A searched CASE only: "CASE x WHEN ..." carries ``this`` and refuses.
    exp.Case: {"ifs": _any, "default": _any},
    exp.If: {"this": _any, "true": _any},
    # CAST, not TRY_CAST (``safe``) and no FORMAT: a value of a built-in type.
    exp.Cast: {"this": _any, "to": _any},
    exp.DataType: {"this": _builtin_type},
    exp.Neg: _THIS,
}
# A subclass of a read class would be read as its parent: it refuses unless listed.
_READ_FAMILIES: tuple[type[exp.Expression], ...] = tuple(_HANDLED)


def _carried(value: object) -> bool:
    return value is not None and value is not False and value != [] and value != ""


def _join_shape(join: exp.Join) -> str:
    """The one shape a join is rendered as, or :class:`DescribeError`.

    ``inner``: [INNER] JOIN ... ON. ``left``: LEFT [OUTER] JOIN ... ON, only as
    the single join after FROM (the rows a LEFT JOIN keeps are the FROM
    table's, and a later join could drop them again). ``semi`` / ``anti``:
    SEMI or ANTI JOIN ... ON. A comma or CROSS join, USING, NATURAL, RIGHT,
    FULL, ASOF, POSITIONAL, LEFT SEMI/ANTI and any other combination refuse.
    """
    kind = str(join.args.get("kind") or "").upper()
    side = str(join.args.get("side") or "").upper()
    if not _carried(join.args.get("on")) or any(
        _carried(value)
        for key, value in join.args.items()
        if key not in {"this", "on", "kind", "side"}
    ):
        raise DescribeError("a join the statement does not describe")
    only = isinstance(join.parent, exp.Select) and join.parent.args.get("joins") == [join]
    if not side:
        if kind in {"", "INNER"}:
            return "inner"
        if kind == "SEMI":
            return "semi"
        if kind == "ANTI":
            return "anti"
    elif side == "LEFT" and kind in {"", "OUTER"} and only:
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
    """The renderer reads every node: audit every node of a read class.

    Runs on the whole parsed tree before ordinals, redaction or naming touch it.
    """
    for node in _walk(tree):
        if isinstance(node, _READ_FAMILIES):
            _audit_node(node, _HANDLED)


# -- step 3: the core grammar ------------------------------------------------------


def _unparen(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _arg(node: exp.Expression, *names: str) -> exp.Expression | None:
    for name in names:
        value = node.args.get(name)
        if isinstance(value, exp.Expression):
            return value
    return None


def _ordinal(node: exp.Expression) -> int | None:
    """The position a GROUP BY or ORDER BY key names, through any parentheses."""
    inner = _unparen(node)
    if isinstance(inner, exp.Literal) and not inner.is_string and inner.is_int:
        return int(inner.name)
    return None


def _is_star(node: exp.Expression) -> bool:
    return isinstance(node, exp.Star) or (
        isinstance(node, exp.Column) and isinstance(node.this, exp.Star)
    )


def _projection_inner(select: exp.Select, position: int) -> exp.Expression:
    projection = select.expressions[position - 1]
    return projection.this if isinstance(projection, exp.Alias) else projection


def _same_output(select: exp.Select, node: exp.Expression) -> int | None:
    """The position of the CASE output ``node`` repeats, by its exact SQL text."""
    text = node.sql(dialect=_DIALECT, comments=False)
    for position in range(1, len(select.expressions) + 1):
        inner = _projection_inner(select, position)
        if isinstance(inner, exp.Case) and inner.sql(dialect=_DIALECT, comments=False) == text:
            return position
    return None


class _Grammar:
    """Accepts the core grammar (module docstring) node by node, or raises.

    Each method accepts one position; :meth:`check` then requires that every
    node of the tree was accepted, so a construct no method names (a subquery,
    a window, a function, a NULL operand) refuses wherever it sits.
    """

    def __init__(self, select: exp.Select) -> None:
        self.select = select
        self.seen: set[int] = set()

    def ok(self, node: exp.Expression) -> None:
        self.seen.add(id(node))

    def identifier(self, node: object) -> None:
        if not isinstance(node, exp.Identifier):
            raise DescribeError("a name the statement does not describe")
        self.ok(node)

    def check(self) -> None:
        select = self.select
        self.ok(select)
        distinct = select.args.get("distinct")
        if distinct is not None:
            if not isinstance(distinct, exp.Distinct) or _carried(distinct.args.get("expressions")):
                raise DescribeError("a DISTINCT ON the statement does not describe")
            self.ok(distinct)
        source = _arg(select, "from_", "from")
        if not isinstance(source, exp.From):
            raise DescribeError("a statement with no table")
        self.ok(source)
        self.relation(source.this)
        for join in select.args.get("joins") or []:
            self.ok(join)
            _join_shape(join)
            self.relation(join.this)
            self.on(join.args["on"])
        for projection in select.expressions:
            self.projection(projection)
        where = select.args.get("where")
        if where is not None:
            self.ok(where)
            self.condition(where.this, aggregates=False)
        group = select.args.get("group")
        if group is not None:
            self.ok(group)
            for item in group.expressions:
                self.group_key(item)
        having = select.args.get("having")
        if having is not None:
            self.ok(having)
            self.condition(having.this, aggregates=True)
        order = select.args.get("order")
        if order is not None:
            self.ok(order)
            for ordered in order.expressions:
                if not isinstance(ordered, exp.Ordered):
                    raise DescribeError("an ORDER BY the statement does not describe")
                self.ok(ordered)
                self.order_key(ordered.this)
                if distinct is not None:
                    self.distinct_order_key(ordered.this)
        limit = select.args.get("limit")
        if limit is not None:
            count = limit.expression
            if not (
                isinstance(limit, exp.Limit)
                and isinstance(count, exp.Literal)
                and not count.is_string
                and count.is_int
            ):
                raise DescribeError("a LIMIT that is not a row count")
            self.ok(limit)
            self.ok(count)
        for node in select.walk():
            if id(node) not in self.seen:
                raise DescribeError(f"a {type(node).__name__} the statement does not describe")

    # -- positions --

    def relation(self, node: exp.Expression) -> None:
        if not isinstance(node, exp.Table):
            raise DescribeError("a source that is not a granted table")
        self.ok(node)
        self.identifier(node.this)
        alias = node.args.get("alias")
        if alias is not None:
            if not isinstance(alias, exp.TableAlias):
                raise DescribeError("a table alias the statement does not describe")
            self.ok(alias)
            self.identifier(alias.this)

    def on(self, node: exp.Expression) -> None:
        """An AND of comparisons and boolean columns: what "on ..." states."""
        self.paren(node)
        node = _unparen(node)
        if isinstance(node, exp.And):
            self.ok(node)
            self.on(node.this)
            self.on(node.expression)
        elif type(node) in _ORDER_COMPARISONS:
            self.comparison(node, aggregates=False)
        elif isinstance(node, exp.Column):
            self.column(node)
        else:
            raise DescribeError("a join condition the statement does not describe")

    def projection(self, node: exp.Expression) -> None:
        if isinstance(node, exp.Alias):
            self.ok(node)
            self.identifier(node.args.get("alias"))
            node = node.this
            if _is_star(node):
                raise DescribeError("a named star")
        if isinstance(node, exp.Star):
            self.ok(node)
        elif isinstance(node, exp.Column):
            self.column(node, star=True)
        elif isinstance(node, exp.Case):
            self.case(node)
        elif not self.aggregate(node):
            raise DescribeError("an output the statement does not describe")

    def column(self, node: exp.Expression, *, star: bool = False) -> None:
        if not isinstance(node, exp.Column):
            raise DescribeError("a column the statement does not describe")
        self.ok(node)
        if isinstance(node.this, exp.Star):
            if not star:
                raise DescribeError("all columns where one value is read")
            self.ok(node.this)
        else:
            self.identifier(node.this)
        if node.args.get("table") is not None:
            self.identifier(node.args.get("table"))

    def aggregate(self, node: exp.Expression) -> bool:
        """COUNT(*), COUNT(1), COUNT(col), COUNT(DISTINCT col), SUM/AVG/MIN/MAX(col)."""
        if type(node) is exp.Count:
            self.ok(node)
            arg = node.this
            if arg is None or isinstance(arg, exp.Star):
                if arg is not None:
                    self.ok(arg)
            elif isinstance(arg, exp.Distinct):
                items = arg.expressions
                if len(items) != 1 or _carried(arg.args.get("on")):
                    raise DescribeError("a COUNT DISTINCT the statement does not describe")
                self.ok(arg)
                self.column(items[0])
            elif isinstance(arg, exp.Literal) and not arg.is_string and arg.is_int:
                self.ok(arg)  # COUNT(1): one non-empty value per row
            else:
                self.column(arg)
            return True
        if type(node) in _AGGREGATES:
            self.ok(node)
            self.column(node.this)
            return True
        return False

    def value(self, node: exp.Expression) -> None:
        """A literal, a negative number or a CAST of a value. Never NULL."""
        if isinstance(node, _VALUE_TYPES):
            self.ok(node)
        elif isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
            if node.this.is_string:
                raise DescribeError("a negated string")
            self.ok(node)
            self.ok(node.this)
        elif type(node) is exp.Cast:
            to = node.args.get("to")
            if not isinstance(to, exp.DataType):
                raise DescribeError("a CAST the statement does not describe")
            if isinstance(_unparen(node.this), exp.Cast):
                # Each CAST can change the value (a decimal cut to an integer,
                # then to text): "a value" would not say which.
                raise DescribeError("a CAST of a CAST")
            self.ok(node)
            self.ok(to)
            self.value(_unparen(node.this))
            self.paren(node.this)
        else:
            # NULL is never a value: ``x <> NULL`` is no IS NOT NULL test.
            raise DescribeError("a value the statement does not describe")

    def paren(self, node: exp.Expression) -> None:
        while isinstance(node, exp.Paren):
            self.ok(node)
            node = node.this

    def operand(self, node: exp.Expression, *, aggregates: bool) -> str:
        """A column, a value or (in HAVING and ORDER BY) an aggregate. Returns which."""
        self.paren(node)
        node = _unparen(node)
        if isinstance(node, exp.Column):
            self.column(node)
            return "column"
        if aggregates and self.aggregate(node):
            return "aggregate"
        self.value(node)
        return "value"

    def comparison(self, node: exp.Expression, *, aggregates: bool) -> None:
        self.ok(node)
        left = self.operand(node.this, aggregates=aggregates)
        right = self.operand(node.expression, aggregates=aggregates)
        if left == right == "value":
            raise DescribeError("a comparison of two values")

    def condition(self, node: exp.Expression, *, aggregates: bool) -> None:
        self.paren(node)
        node = _unparen(node)
        kind = type(node)
        if kind in (exp.And, exp.Or):
            self.ok(node)
            self.condition(node.this, aggregates=aggregates)
            self.condition(node.expression, aggregates=aggregates)
        elif kind is exp.Not:
            self.ok(node)
            self.condition(node.this, aggregates=aggregates)
        elif kind in _ORDER_COMPARISONS:
            self.comparison(node, aggregates=aggregates)
        elif kind in (exp.Like, exp.ILike):
            self.ok(node)
            if self.operand(node.this, aggregates=aggregates) == "value":
                raise DescribeError("a pattern match on a value")
            self.operand(node.expression, aggregates=aggregates)
        elif kind is exp.In:
            self.ok(node)
            if self.operand(node.this, aggregates=aggregates) == "value":
                raise DescribeError("a list test on a value")
            if not node.expressions:
                raise DescribeError("an empty list")
            for item in node.expressions:
                self.paren(item)
                self.value(_unparen(item))
        elif kind is exp.Between:
            self.ok(node)
            if self.operand(node.this, aggregates=aggregates) == "value":
                raise DescribeError("a range test on a value")
            for bound in (node.args.get("low"), node.args.get("high")):
                if not isinstance(bound, exp.Expression):
                    raise DescribeError("a range with no bound")
                self.paren(bound)
                self.value(_unparen(bound))
        elif kind is exp.Is:
            self.ok(node)
            if self.operand(node.this, aggregates=aggregates) == "value":
                raise DescribeError("an empty test on a value")
            if not isinstance(node.expression, exp.Null):
                raise DescribeError("an IS test the statement does not describe")
            self.ok(node.expression)
        elif kind is exp.Column:
            self.column(node)  # a boolean column, read as itself
        else:
            raise DescribeError(f"a {kind.__name__} condition the statement does not describe")

    def case(self, node: exp.Expression) -> None:
        """A searched CASE: conditions in the grammar (no aggregate), values as labels."""
        if not isinstance(node, exp.Case) or not node.args.get("ifs"):
            raise DescribeError("a CASE the statement does not describe")
        self.ok(node)
        for branch in node.args["ifs"]:
            if not isinstance(branch, exp.If):
                raise DescribeError("a CASE branch the statement does not describe")
            self.ok(branch)
            self.condition(branch.this, aggregates=False)
            self.paren(branch.args["true"])
            self.value(_unparen(branch.args["true"]))
        default = node.args.get("default")
        if default is not None:
            self.paren(default)
            self.value(_unparen(default))

    def group_key(self, node: exp.Expression) -> None:
        if _ordinal(node) is not None:
            self.paren(node)
            self.ok(_unparen(node))
        elif isinstance(_unparen(node), exp.Case):
            self.paren(node)
            if _same_output(self.select, _unparen(node)) is None:
                raise DescribeError("a grouping by a CASE that is not an output")
            self.case(_unparen(node))
        else:
            self.paren(node)
            self.column(_unparen(node))

    def distinct_order_key(self, node: exp.Expression) -> None:
        """Under DISTINCT, a sort key must be an output.

        DuckDB runs ``SELECT DISTINCT x ... ORDER BY y`` by sorting each distinct
        row by ``first(y)`` of an arbitrary source row (PICK-READING-01 fuzz:
        ``SELECT DISTINCT txn_type ... ORDER BY sku DESC LIMIT 1`` was stated
        "sorted by sku descending" and served a row no such sort picks). So a key
        is accepted only when it is an ordinal, a bare name of an output's alias
        (a whole ORDER BY key binds the alias first) or an expression whose SQL
        text is that of an output. Anything else refuses.
        """
        inner = _unparen(node)
        if _ordinal(inner) is not None:
            return
        if isinstance(inner, exp.Column) and not inner.table:
            name = fold_identifier(inner.name)
            if any(
                isinstance(p, exp.Alias) and p.alias and fold_identifier(p.alias) == name
                for p in self.select.expressions
            ):
                return
        text = inner.sql(dialect=_DIALECT, comments=False)
        for position in range(1, len(self.select.expressions) + 1):
            output = _projection_inner(self.select, position)
            if output.sql(dialect=_DIALECT, comments=False) == text:
                return
        raise DescribeError("a DISTINCT sorted by a key that is not an output")

    def order_key(self, node: exp.Expression) -> None:
        self.paren(node)
        inner = _unparen(node)
        if _ordinal(inner) is not None:
            self.ok(inner)
        elif isinstance(inner, exp.Column):
            self.column(inner)
        elif not self.aggregate(inner):
            raise DescribeError("a sort key the statement does not describe")


# -- step 4: ordinals ---------------------------------------------------------------


def _resolve_ordinals(select: exp.Select) -> dict[int, int]:
    """Replace each ordinal (and each GROUP BY CASE repeating an output) with a
    placeholder mark. Returns id(placeholder) -> position.

    The placeholder is named by the output it points at (step 6), which also
    refuses any reference in a list that expands a star.
    """
    refs: dict[int, int] = {}
    count = len(select.expressions)
    items: list[exp.Expression] = []
    group = select.args.get("group")
    if isinstance(group, exp.Group):
        items.extend(group.expressions)
    order = select.args.get("order")
    if isinstance(order, exp.Order):
        items.extend(o.this for o in order.expressions if isinstance(o, exp.Ordered))
    for item in items:
        position = _ordinal(item)
        if position is None and isinstance(_unparen(item), exp.Case):
            position = _same_output(select, _unparen(item))
            if position is None:  # pragma: no cover - the grammar refused it
                raise DescribeError("a grouping by a CASE that is not an output")
        elif position is not None and (position < 1 or position > count):
            raise DescribeError(f"ordinal {position} is out of range")
        if position is None:
            continue
        mark = exp.Var(this=MARK)
        item.replace(mark)
        refs[id(mark)] = position
    return refs


# -- step 5: redaction --------------------------------------------------------------


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


# -- step 6: names -------------------------------------------------------------------


def _approve(approved: set[int], name: str) -> exp.Identifier:
    ident = exp.to_identifier(name)
    approved.add(id(ident))
    return ident


def _reference(select: exp.Select, position: int, approved: set[int]) -> exp.Expression:
    """What a reference to output ``position`` is shown as: never its alias."""
    inner = _projection_inner(select, position)
    if _is_star(inner):
        raise DescribeError("a reference to all columns")
    if isinstance(inner, exp.Column):
        label = _column(inner)
    else:
        label = _aggregate(inner) or f"{COMPUTED} {position}"
    var = exp.Var(this=label)
    approved.add(id(var))
    return var


def _grouped_names(select: exp.Select, ordinals: dict[int, int]) -> set[str] | None:
    """Folded names of the columns GROUP BY lists. None when it lists anything else.

    An ordinal counts as the output it names. A CASE gives None: how DuckDB
    binds a HAVING name against it is not established here, so it is not guessed.
    """
    group = select.args.get("group")
    if group is None:
        return set()
    names: set[str] = set()
    for item in group.expressions:
        inner = _projection_inner(select, ordinals[id(item)]) if id(item) in ordinals else item
        inner = _unparen(inner)
        if not isinstance(inner, exp.Column) or isinstance(inner.this, exp.Star):
            return None
        names.add(fold_identifier(inner.name))
    return names


def _bare_order_key(column: exp.Column) -> bool:
    """Whether ``column`` is a whole ORDER BY key: only parentheses around it."""
    node: exp.Expression = column
    parent = node.parent
    while isinstance(parent, exp.Paren):
        node, parent = parent, parent.parent
    return isinstance(parent, exp.Ordered) and node.arg_key == "this"


def _clause(column: exp.Column, select: exp.Select) -> tuple[str, bool, exp.Expression]:
    """The SELECT clause ``column`` sits in, whether it is inside an aggregate,
    and the clause's top node (for a join: that :class:`exp.Join`)."""
    node: exp.Expression = column
    in_aggregate = False
    while node.parent is not None and node.parent is not select:
        if isinstance(node.parent, exp.AggFunc):
            in_aggregate = True
        node = cast(exp.Expression, node.parent)
    if node.parent is not select:  # pragma: no cover - every column is under the select
        raise DescribeError("a name outside the statement")
    return str(node.arg_key or ""), in_aggregate, node


def _alias_position(column: exp.Column, select: exp.Select) -> bool:
    """Whether bare ``column`` sits where DuckDB binds an output's alias first.

    Only two places: a whole ORDER BY key (parentheses aside), and a HAVING
    name outside any aggregate (where :func:`_binds_to_alias` still applies
    the GROUP BY rule). Everywhere else DuckDB binds a real column first, and
    a real column the payload does not list is one describe cannot see.
    """
    if column.table:
        return False
    clause, in_aggregate, _top = _clause(column, select)
    if in_aggregate:
        return False
    return clause == "having" or (clause == "order" and _bare_order_key(column))


def _passthrough(projection: exp.Expression) -> bool:
    """An output that is one column under its own name (``sku AS sku``)."""
    if not isinstance(projection, exp.Alias):
        return False
    inner = projection.this
    return (
        isinstance(inner, exp.Column)
        and not isinstance(inner.this, exp.Star)
        and fold_identifier(inner.name) == fold_identifier(projection.alias)
    )


def _check_output_names(select: exp.Select, relation_names: set[str]) -> None:
    """Refuse an output name a reader of the SQL could take for another name.

    The catalog lists the payload's columns, but DuckDB binds against every
    real column of a table, and the payload may list a subset (PICK-READING-01
    review: ``SELECT sku AS reference_doc ... WHERE reference_doc IS NULL`` was
    stated as "where sku is empty" while DuckDB tested the real, unlisted
    ``reference_doc``; ``MAX(sku) AS location_id ... GROUP BY location_id`` was
    "for each largest sku" while DuckDB grouped by the real ``location_id``).
    describe cannot know an unlisted column, so an output alias that is not a
    plain passthrough (:func:`_passthrough`) is accepted only when no other
    name of the statement could be it: not a relation's name, not a
    qualifier, and every column reference spelled like it sits where DuckDB
    binds the alias first (:func:`_alias_position`). A bare name in WHERE,
    GROUP BY, ON, the SELECT list or an aggregate that equals such an alias
    therefore refuses, listed column or not.
    """
    names: dict[str, list[exp.Expression]] = {}
    for projection in select.expressions:
        if isinstance(projection, exp.Alias) and projection.alias:
            names.setdefault(fold_identifier(projection.alias), []).append(projection)
    for name, projections in names.items():
        if all(_passthrough(p) for p in projections):
            continue
        if name in relation_names:
            raise DescribeError("an output named like a table of the statement")
        for column in select.find_all(exp.Column):
            if column.table and fold_identifier(column.table) == name:
                raise DescribeError("an output named like a qualifier of the statement")
            if isinstance(column.this, exp.Star) or fold_identifier(column.name) != name:
                continue
            if not _alias_position(column, select):
                raise DescribeError("an output named like a column the statement reads")


def _binds_to_alias(
    column: exp.Column, select: exp.Select, grouped: set[str] | None, is_column: bool
) -> bool:
    """Whether DuckDB binds bare ``column``, also an output's alias, to that output.

    DuckDB's rules (checked against DuckDB itself; pinned by
    ``tests/test_plan_sql/test_plan_sql_describe.py``):

    * Inside an aggregate's arguments, the SELECT list, JOIN ON, WHERE and
      GROUP BY: the column. (DuckDB takes the alias in WHERE and GROUP BY when
      no column has the name, but a column the payload does not list can have
      it, so a non-passthrough alias there refused in
      :func:`_check_output_names`.)
    * ORDER BY: the alias when the name is the whole key (parentheses aside).
    * HAVING: the column when GROUP BY lists it, otherwise the alias.

    Anything else (a HAVING over a grouping :func:`_grouped_names` does not
    resolve) raises :class:`DescribeError`.
    """
    clause, in_aggregate, _top = _clause(column, select)
    if in_aggregate or clause in {"expressions", "joins", "where", "group"}:
        return False
    if clause == "order":
        return _bare_order_key(column)
    if clause == "having":
        if not is_column:
            return True
        if grouped is None:
            raise DescribeError("a HAVING name whose grouping is not resolved")
        return fold_identifier(column.name) not in grouped
    raise DescribeError("a name whose binding is not known")


def _bare_condition(column: exp.Column) -> bool:
    """Whether ``column`` is a whole condition (``WHERE flag``), parentheses aside."""
    node: exp.Expression = column
    parent = node.parent
    while isinstance(parent, exp.Paren):
        node, parent = parent, parent.parent
    key = node.arg_key
    if isinstance(parent, (exp.Where, exp.Having, exp.Not)):
        return key == "this"
    if isinstance(parent, (exp.And, exp.Or)):
        return True
    if isinstance(parent, exp.If):
        return key == "this"
    if isinstance(parent, exp.Join):
        return key == "on"
    return False


class _Relation:
    """One table of the statement: its catalog key, the name it is read by, and
    how it joins (``from``, or a :func:`_join_shape`), at position ``index``."""

    def __init__(self, node: exp.Table, key: str, shape: str, index: int) -> None:
        self.node = node
        self.key = key
        self.name = fold_identifier(node.alias_or_name)
        self.shape = shape
        self.index = index


#: Relations whose columns the rest of the statement can read. A SEMI or ANTI
#: join's table is visible only in that join's own ON (DuckDB: "Referenced
#: table ... not found" anywhere else, and a bare name binds elsewhere).
_VISIBLE_SHAPES = frozenset({"from", "inner", "left"})


def _relations(select: exp.Select, catalog: _Catalog) -> list[_Relation]:
    source = _arg(select, "from_", "from")
    if not isinstance(source, exp.From) or not isinstance(source.this, exp.Table):
        raise DescribeError("a statement with no table")  # pragma: no cover - grammar
    entries: list[tuple[exp.Expression, str]] = [(source.this, "from")]
    entries += [(join.this, _join_shape(join)) for join in select.args.get("joins") or []]
    tables = list(select.find_all(exp.Table))
    if len(tables) != len(entries):  # pragma: no cover - the grammar allows no other table
        raise DescribeError("a table the statement does not describe")
    relations: list[_Relation] = []
    for index, (node, shape) in enumerate(entries):
        if not isinstance(node, exp.Table):  # pragma: no cover - grammar
            raise DescribeError("a source that is not a granted table")
        key = fold_identifier(node.name)
        if key not in catalog.tables:
            raise DescribeError("a table that is not granted")
        relations.append(_Relation(node, key, shape, index))
    return relations


def _scope(relations: list[_Relation], clause: str, top: exp.Expression) -> list[_Relation]:
    """The relations a column in ``clause`` can read.

    A join's ON reads the visible relations before it and its own table.
    Every other clause reads FROM and the INNER and LEFT joins: never a SEMI or
    ANTI join's table (PICK-READING-01 review: ``... SEMI JOIN inventory ON ...
    WHERE unit_cost_myr > 50`` was stated against inventory's column while
    DuckDB read the transactions column the payload does not list).
    """
    if clause == "joins":
        select = top.parent
        joins = select.args.get("joins") if isinstance(select, exp.Select) else None
        position = next((i for i, j in enumerate(joins or []) if j is top), None)
        if position is None:  # pragma: no cover - a join column is under its join
            raise DescribeError("a join the statement does not describe")
        own = position + 1
        return [
            r for r in relations if r.index == own or (r.index < own and r.shape in _VISIBLE_SHAPES)
        ]
    return [r for r in relations if r.shape in _VISIBLE_SHAPES]


def _name(select: exp.Select, catalog: _Catalog, ordinals: dict[int, int]) -> set[int]:
    """Rewrite every name in ``select`` to granted text. Returns the approved node ids.

    A table must be granted and read once; its alias is dropped and a column
    qualified by it is shown qualified by the granted table. A column must be a
    granted column of exactly one table its clause can read (:func:`_scope`),
    printed in the payload's spelling; a bare one must be a name no other table
    of the statement lists, so it cannot be read as that table's. A bare name that is an
    output's alias where DuckDB binds the alias, and an ordinal, is shown as
    what that output is (:func:`_reference`). Every output alias is dropped.
    Anything else raises, and so do: an output name another name could be
    (:func:`_check_output_names`); a WHERE, or a HAVING outside an aggregate
    (directly or through an output's name), that reads the table of a LEFT
    JOIN (it drops the unmatched rows the statement says are kept); and a bare
    column read as a condition unless the payload types it BOOLEAN.
    """
    approved: set[int] = set()
    relations = _relations(select, catalog)
    keys = [r.key for r in relations]
    if len(set(keys)) != len(keys):
        raise DescribeError("a table read twice")
    listed = {folded for key in keys for folded in catalog.columns.get(key, {})}
    names: set[str] = set()
    for relation in relations:
        if relation.name in names:
            raise DescribeError("two relations with one name")
        if relation.name != relation.key and (
            relation.name in catalog.tables or relation.name in listed
        ):
            raise DescribeError("a relation alias that is another table's or a column's name")
        names.add(relation.name)
    _check_output_names(select, names | set(keys))
    counts: dict[str, int] = {}
    for key in keys:
        for folded in catalog.columns.get(key, {}):
            counts[folded] = counts.get(folded, 0) + 1
    shared = {folded for folded, count in counts.items() if count > 1}
    aliases: dict[str, list[int]] = {}
    for position, projection in enumerate(select.expressions, 1):
        if isinstance(projection, exp.Alias) and projection.alias:
            aliases.setdefault(fold_identifier(projection.alias), []).append(position)
    grouped = _grouped_names(select, ordinals)

    pending: dict[int, int] = dict(ordinals)
    left_reads: set[int] = set()  # columns that read the table of a LEFT JOIN
    for column in list(select.find_all(exp.Column)):
        clause, in_aggregate, top = _clause(column, select)
        scope = _scope(relations, clause, top)
        qualified: list[_Relation] = []
        if column.table:
            qualifier = fold_identifier(column.table)
            qualified = [r for r in scope if r.name == qualifier]
            if not qualified:
                raise DescribeError("a qualifier that is not a table this clause reads")
        if isinstance(column.this, exp.Star):
            if not qualified:  # pragma: no cover - a bare star is exp.Star, not a column
                raise DescribeError("all columns of no table")
            column.set("table", _approve(approved, catalog.tables[qualified[0].key]))
            continue
        folded = fold_identifier(column.name)
        owners = [
            r
            for r in (qualified if column.table else scope)
            if folded in catalog.columns.get(r.key, {})
        ]
        positions = aliases.get(folded, []) if not column.table else []
        if positions and _binds_to_alias(column, select, grouped, bool(owners)):
            if len(positions) > 1:
                # DuckDB takes one of the outputs that share the name; say none.
                raise DescribeError("a name that more than one output is called")
            if _bare_condition(column):
                raise DescribeError("an output read as a condition")
            pending[id(column)] = positions[0]
            continue
        if not owners:
            raise DescribeError("a name that is not a granted column")
        if len(owners) > 1:
            raise DescribeError("a name more than one table of the clause has")
        owner = owners[0]
        if owner.shape == "left":
            if clause == "where":
                # "keeping rows of t with no match" is false once WHERE reads the
                # joined table: an unmatched row's columns are empty there.
                raise DescribeError("a WHERE that reads the table of a LEFT JOIN")
            if clause == "having" and not in_aggregate:
                # The same for a HAVING that tests the joined table's grouped
                # value: the groups of unmatched rows hold an empty one.
                raise DescribeError("a HAVING that reads the table of a LEFT JOIN")
            if not in_aggregate:
                left_reads.add(id(column))
        if _bare_condition(column) and catalog.types.get(owner.key, {}).get(folded) not in (
            _BOOLEAN_TYPES
        ):
            # DuckDB casts any other type to a truth value ("where a" keeps a <> 0).
            raise DescribeError("a column read as a condition that is not typed BOOLEAN")
        if not column.table and folded in shared:
            # "where a" after "... some match in u" reads as u's a: DuckDB binds
            # t's. A bare name is printed bare, so it must be the only one.
            raise DescribeError("a bare name another table of the statement lists")
        column.set("this", _approve(approved, catalog.columns[owner.key][folded]))
        if column.table:
            column.set("table", _approve(approved, catalog.tables[owner.key]))
    for relation in relations:
        relation.node.set("this", _approve(approved, catalog.tables[relation.key]))
        relation.node.set("alias", None)

    references = [node for node in select.walk() if id(node) in pending]
    for node in references:
        if isinstance(node, exp.Column) and _clause(node, select)[0] == "having":
            output = _projection_inner(select, pending[id(node)])
            if any(id(c) in left_reads for c in output.find_all(exp.Column)):
                # A HAVING name bound to an output that reads the joined table
                # outside an aggregate tests that table's value, as above.
                raise DescribeError("a HAVING that reads the table of a LEFT JOIN")
    if references and any(
        _is_star(_projection_inner(select, i)) for i in range(1, len(select.expressions) + 1)
    ):
        # Which output a name or position means depends on the star's columns.
        raise DescribeError("a reference to an output of a list that expands a star")
    for node in references:
        node.replace(_reference(select, pending[id(node)], approved))
    for projection in list(select.expressions):
        if isinstance(projection, exp.Alias):
            projection.replace(projection.this)
    return approved


# -- step 7: fail-closed leaf check -------------------------------------------------


def _children(node: exp.Expression) -> list[exp.Expression]:
    return list(node.iter_expressions())


def _allowed_leaf(node: exp.Expression, kept: set[int], approved: set[int]) -> bool:
    if isinstance(node, exp.Identifier):
        return id(node) in approved
    if isinstance(node, exp.DataType):
        return _builtin_type(node.this)
    if isinstance(node, (exp.Star, exp.Null, exp.Distinct)):
        return True
    if isinstance(node, exp.Var):
        return node.name == MARK or id(node) in approved
    if _is_value(node):
        return id(node) in kept
    return False


def _leaf_check(tree: exp.Expression, kept: set[int], approved: set[int]) -> None:
    for node in _walk(tree):
        if node.comments:
            raise DescribeError("a comment survived stripping")
        if _children(node):
            continue
        if not _allowed_leaf(node, kept, approved):
            raise DescribeError(f"unknown leaf {type(node).__name__}")


# -- rendering ----------------------------------------------------------------------


def _shown(name: str) -> str:
    """A granted name as printed: quoted when it is not a plain word, or when it is
    a word of the statement's own phrases, so it cannot read as one."""
    if _PLAIN_NAME.fullmatch(name) and name.lower() not in VOCABULARY | _VALUE_WORDS:
        return name
    return '"' + name.replace('"', '""') + '"'


def _aggregate(node: exp.Expression) -> str:
    """The words for an aggregate call, or ``""`` when ``node`` is not one."""
    if type(node) is exp.Count:
        arg = node.this
        if arg is None or isinstance(arg, (exp.Star, exp.Var, exp.Literal)):
            # COUNT(*), COUNT() and COUNT(1) (its 1 is redacted or kept).
            return "number of rows"
        if isinstance(arg, exp.Distinct):
            return f"number of distinct {_join_words(_operand(e) for e in arg.expressions)}"
        return f"number of non-empty {_operand(arg)}"
    for kind, word in _AGGREGATES.items():
        if type(node) is kind:
            return f"{word} {_operand(node.this)}"
    return ""


def _column(node: exp.Column) -> str:
    """A column already rewritten to granted names by :func:`_name`."""
    table = node.table
    return f"{_shown(table)}.{_shown(node.name)}" if table else _shown(node.name)


def _operand(node: exp.Expression | None) -> str:
    if node is None:
        raise DescribeError("an operand with nothing in it")
    node = _unparen(node)
    if isinstance(node, exp.Column):
        if isinstance(node.this, exp.Star):
            # ``t.*`` in a join is t's columns only, never "all columns".
            return f"all columns of {_shown(node.table)}" if node.table else "all columns"
        return _column(node)
    if isinstance(node, exp.Var):
        return node.name  # the mark, or a reference already named (step 6)
    if type(node) is exp.Cast and isinstance(_unparen(node.this), exp.Var):
        return MARK  # the value inside was redacted: its type is not a value
    words = _aggregate(node)
    if words:
        return words
    if _is_value(node) or isinstance(node, (exp.Neg, exp.Cast)):
        return node.sql(dialect=_DIALECT, comments=False)  # a literal keep_literal kept
    raise DescribeError(f"a {type(node).__name__} the statement does not describe")


def _join_words(parts: Iterable[str], word: str = "and") -> str:
    items = [p for p in parts if p]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {word} " + items[-1]


def _part(node: exp.Expression, parent: type[exp.Expression]) -> str:
    """One side of an AND or OR, in parentheses when it is the other connective,
    so "a or b and c" is never printed: its grouping is always shown."""
    inner = _unparen(node)
    text = _condition(inner)
    if isinstance(inner, (exp.And, exp.Or)) and type(inner) is not parent:
        return f"({text})"
    return text


def _condition(node: exp.Expression | None) -> str:
    if node is None:
        raise DescribeError("a condition with nothing in it")
    node = _unparen(node)
    if isinstance(node, exp.And):
        return f"{_part(node.this, exp.And)} and {_part(node.expression, exp.And)}"
    if isinstance(node, exp.Or):
        return f"{_part(node.this, exp.Or)} or {_part(node.expression, exp.Or)}"
    if isinstance(node, exp.Not):
        inner = _unparen(node.this)
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
    if isinstance(node, exp.Column):
        return _operand(node)
    raise DescribeError(f"a {type(node).__name__} condition the statement does not describe")


def _case(node: exp.Expression) -> str:
    """A searched CASE in words, in its own order: each branch, then the rest."""
    parts = [
        f"when {_condition(branch.this)} then {_operand(branch.args['true'])}"
        for branch in node.args["ifs"]
    ]
    default = node.args.get("default")
    parts.append(f"otherwise {_operand(default)}" if default is not None else "otherwise empty")
    return ", ".join(parts)


def _projection(node: exp.Expression, position: int) -> str:
    """One output, never under its alias (step 6 dropped every output alias)."""
    if isinstance(node, exp.Star):
        return "all columns"
    if isinstance(node, exp.Column):
        return _operand(node)
    if isinstance(node, exp.Case):
        return f"{COMPUTED} {position} ({_case(node)})"
    words = _aggregate(node)
    if words:
        return words
    raise DescribeError("an output the statement does not describe")


def _table(node: exp.Expression) -> str:
    if isinstance(node, exp.Table):
        return _shown(node.name)
    raise DescribeError("a source that is not a granted table")


def _from_text(select: exp.Select) -> str:
    """The FROM table, then each join in the words of its shape (:func:`_join_shape`)."""
    source = _arg(select, "from_", "from")
    if not isinstance(source, exp.From):
        raise DescribeError("a statement with no table")
    first = _table(source.this)
    text = first
    for join in select.args.get("joins") or []:
        shape = _join_shape(join)
        target = _table(join.this)
        condition = _condition(_arg(join, "on"))
        if shape == "inner":
            text += f", matched to {target} on {condition}"
        elif shape == "left":
            text += f", matched to {target} on {condition}, keeping rows of {first} with no match"
        elif shape == "semi":
            text += f", keeping only rows with some match in {target} on {condition}"
        elif shape == "anti":
            text += f", keeping only rows with no match in {target} on {condition}"
        else:  # pragma: no cover - _join_shape returns only the shapes above
            raise DescribeError("a join the statement does not describe")
    return text


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
    text += f" from {_from_text(select)}"
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
            if not isinstance(ordered, exp.Ordered):  # pragma: no cover - the grammar refused it
                raise DescribeError("an ORDER BY the statement does not describe")
            key = _operand(ordered.this)
            if ordered.args.get("desc"):
                key += " descending"
            # DuckDB sorts empty values last in both directions unless told
            # otherwise; NULLS LAST is that default, so only NULLS FIRST is said.
            if ordered.args.get("nulls_first"):
                key += " with empty values first"
            keys.append(key)
        tail.append("sorted by " + ", ".join(keys))
    limit = select.args.get("limit")
    if isinstance(limit, exp.Limit):
        literal = limit.expression
        if not isinstance(literal, exp.Literal) or literal.is_string or not literal.is_int:
            raise DescribeError("a LIMIT that is not a row count")  # pragma: no cover - grammar
        count = int(literal.name)  # "LIMIT 007" is seven rows, said as "7"
        tail.append(f"first {count} {'row' if count == 1 else 'rows'}")
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
