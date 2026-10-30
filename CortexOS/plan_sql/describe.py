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
2. Resolve positional ``GROUP BY 1`` / ``ORDER BY 2`` to the projection they
   name, so an ordinal is not shown as a value. Out of range raises.
3. Redact every value leaf in the whole tree (every clause, subqueries and
   CTEs included) to :data:`MARK`: ``exp.Literal``, ``exp.Boolean`` and the
   string forms that are not ``exp.Literal`` subclasses. A minus directly over
   a value goes with it. The one exemption is the count of a ``LIMIT``, shown
   as "first N rows"; a leaf ``keep_literal`` accepts is also kept.
4. Fail closed: every remaining leaf must be a kind this module knows. Any
   other leaf (a JSON path part, a placeholder, a node a later sqlglot adds)
   or a comment that survived step 1 raises :class:`DescribeError`; the caller
   then offers nothing.

``keep_literal`` is display redaction only: it decides whether a literal that
is already in the gated SQL is printed or shown as :data:`MARK`. It never
writes, picks, gates or routes SQL.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import cast

import sqlglot
from sqlglot import exp

#: What every redacted value is shown as.
MARK = "<a value>"
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

_AGGREGATES: dict[type[exp.Expression], str] = {
    exp.Sum: "total of",
    exp.Avg: "average of",
    exp.Min: "smallest",
    exp.Max: "largest",
}

_COMPARISONS: dict[type[exp.Expression], str] = {
    exp.EQ: "is",
    exp.NEQ: "is not",
    exp.GT: "is more than",
    exp.GTE: "is at least",
    exp.LT: "is less than",
    exp.LTE: "is at most",
    exp.Like: "matches",
    exp.ILike: "matches, ignoring case,",
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
    "all columns",
    "for each",
    "and",
    "or",
    "not",
    "from",
    "matched to",
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
    "is one of",
    "is between",
    "is empty",
    "is not empty",
    "sorted by",
    "descending",
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
    "shortened",
)
VOCABULARY: frozenset[str] = frozenset(
    word.strip(",()").lower() for phrase in _PHRASES for word in phrase.split() if word.strip(",()")
)


class DescribeError(ValueError):
    """The SQL cannot be described without risking a value: offer nothing."""


def cap_statement(text: str) -> str:
    """``text`` cut to :data:`STATEMENT_CAP` characters plus the shortened marker.

    At most ``STATEMENT_CAP + len(SHORTENED)`` (312) characters.
    """
    if len(text) <= STATEMENT_CAP:
        return text
    return text[:STATEMENT_CAP] + SHORTENED


def statement(sql: str, *, keep_literal: Callable[[str], bool]) -> str:
    """A plain-language statement of ``sql`` in which no redacted value appears.

    Raises :class:`DescribeError` on a parse failure, an out-of-range ordinal,
    an unknown leaf or a comment that survived stripping.
    """
    try:
        tree = cast("exp.Expression | None", sqlglot.parse_one(sql, read=_DIALECT))
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refusal
        raise DescribeError(f"unparsable: {type(exc).__name__}") from None
    if tree is None:
        raise DescribeError("empty statement")
    _strip_comments(tree)
    _resolve_ordinals(tree)
    kept = _redact(tree, keep_literal)
    _leaf_check(tree, kept)
    return cap_statement(_render(tree))


def _walk(tree: exp.Expression) -> list[exp.Expression]:
    """Every node of ``tree``, materialized so the tree can be edited while looping."""
    return [cast(exp.Expression, node) for node in tree.walk()]


# -- step 1: comments ---------------------------------------------------------------


def _strip_comments(tree: exp.Expression) -> None:
    for node in _walk(tree):
        node.comments = None


# -- step 2: ordinals ---------------------------------------------------------------


def _ordinal(node: exp.Expression) -> int | None:
    if isinstance(node, exp.Literal) and not node.is_string and node.is_int:
        return int(node.name)
    return None


def _projection_for(select: exp.Select, position: int) -> exp.Expression:
    projections = select.expressions
    if position < 1 or position > len(projections):
        raise DescribeError(f"ordinal {position} is out of range")
    chosen = projections[position - 1]
    if isinstance(chosen, exp.Alias) and chosen.alias:
        return exp.column(chosen.alias, quoted=chosen.args["alias"].quoted)
    return (chosen.this if isinstance(chosen, exp.Alias) else chosen).copy()


def _resolve_ordinals(tree: exp.Expression) -> None:
    for select in list(tree.find_all(exp.Select)):
        group = select.args.get("group")
        if isinstance(group, exp.Group):
            for item in list(group.expressions):
                position = _ordinal(item)
                if position is not None:
                    item.replace(_projection_for(select, position))
        order = select.args.get("order")
        if isinstance(order, exp.Order):
            for ordered in order.expressions:
                position = _ordinal(ordered.this) if isinstance(ordered, exp.Ordered) else None
                if position is not None:
                    ordered.this.replace(_projection_for(select, position))


# -- step 3: redaction --------------------------------------------------------------


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


# -- step 4: fail-closed leaf check -------------------------------------------------


def _children(node: exp.Expression) -> list[exp.Expression]:
    return list(node.iter_expressions())


def _allowed_leaf(node: exp.Expression, kept: set[int]) -> bool:
    if isinstance(node, (exp.Identifier, exp.Star, exp.Null, exp.DataType, exp.Distinct)):
        return True
    if isinstance(node, exp.Var):
        return node.name == MARK or node.arg_key == "unit" or isinstance(node.parent, exp.Extract)
    if _is_value(node):
        return id(node) in kept
    if isinstance(node, exp.Func):
        return not _children(node)
    return False


def _leaf_check(tree: exp.Expression, kept: set[int]) -> None:
    for node in _walk(tree):
        if node.comments:
            raise DescribeError("a comment survived stripping")
        if _children(node):
            continue
        if not _allowed_leaf(node, kept):
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
    table = node.table
    return f"{table}.{node.name}" if table else node.name


def _operand(node: exp.Expression | None) -> str:
    if node is None:
        return ""
    if isinstance(node, exp.Paren):
        return _operand(node.this)
    if isinstance(node, exp.Column):
        if isinstance(node.this, exp.Star):
            return "all columns"
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
    for kind, word in _COMPARISONS.items():
        if type(node) is kind:
            return f"{_operand(node.this)} {word} {_operand(node.expression)}"
    if isinstance(node, exp.In) and node.expressions:
        values = ", ".join(_operand(v) for v in node.expressions)
        return f"{_operand(node.this)} is one of ({values})"
    if isinstance(node, exp.Between):
        low, high = node.args.get("low"), node.args.get("high")
        return f"{_operand(node.this)} is between {_operand(low)} and {_operand(high)}"
    return _sql(node)


def _projection(node: exp.Expression) -> str:
    alias = node.alias if isinstance(node, exp.Alias) else ""
    inner = node.this if isinstance(node, exp.Alias) else node
    named = f" as {alias}" if alias else ""
    if isinstance(inner, exp.Star):
        return "all columns"
    if isinstance(inner, exp.Column):
        text = _operand(inner)
        return f"{text}{named}" if alias and alias != inner.name else text
    words = _aggregate(inner)
    if words:
        return f"{words}{named}"
    label = f"a computed value {alias}" if alias else "a computed value"
    return f"{label} ({_sql(inner)})"


def _table(node: exp.Expression) -> str:
    if isinstance(node, exp.Table):
        return node.name
    return _sql(node)


def _arg(node: exp.Expression, *names: str) -> exp.Expression | None:
    for name in names:
        value = node.args.get(name)
        if isinstance(value, exp.Expression):
            return value
    return None


def _from_text(select: exp.Select) -> str:
    source = _arg(select, "from_", "from")
    parts = [_table(source.this)] if isinstance(source, exp.From) else []
    for join in select.args.get("joins") or []:
        target = _table(join.this)
        on = _arg(join, "on")
        using = join.args.get("using") or []
        if on is not None:
            parts.append(f"matched to {target} on {_condition(on)}")
        elif using:
            parts.append(f"matched to {target} on {_join_words(_operand(u) for u in using)}")
        else:
            parts.append(f"matched to {target}")
    return ", ".join(p for p in parts if p)


def _simple(tree: exp.Expression) -> bool:
    if not isinstance(tree, exp.Select):
        return False
    if tree.args.get("with") or tree.args.get("with_"):
        return False
    return not any(
        isinstance(node, (exp.Subquery, exp.CTE, exp.Window, exp.SetOperation))
        for node in tree.walk()
        if node is not tree
    )


def _limit_text(select: exp.Expression) -> list[str]:
    parts: list[str] = []
    offset = select.args.get("offset")
    if isinstance(offset, exp.Offset):
        parts.append(f"skipping {_operand(offset.expression)} rows")
    limit = select.args.get("limit")
    if isinstance(limit, exp.Limit) and limit.expression is not None:
        count = _operand(limit.expression)
        parts.append(f"first {count} {'row' if count == '1' else 'rows'}")
    return parts


def _render_select(select: exp.Select) -> str:
    projections = _join_words(_projection(p) for p in select.expressions)
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
            keys.append(key)
        tail.append("sorted by " + ", ".join(keys))
    tail.extend(_limit_text(select))
    if tail:
        text += ", " + ", ".join(tail)
    return text


def _output_names(tree: exp.Expression) -> str:
    select: exp.Expression | None = tree
    while isinstance(select, exp.SetOperation):
        select = select.this
    if not isinstance(select, exp.Select):
        return "a computed value"
    names = []
    for projection in select.expressions:
        name = projection.alias_or_name
        if isinstance(projection, exp.Star) or (
            isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
        ):
            names.append("all columns")
        elif name and name != MARK:
            names.append(name)
        else:
            names.append("a computed value")
    return _join_words(names)


def _render_fallback(tree: exp.Expression) -> str:
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    tables = sorted({t.name for t in tree.find_all(exp.Table) if t.name and t.name not in ctes})
    nested = len(list(tree.find_all(exp.Subquery))) + len(ctes)
    combined = len(list(tree.find_all(exp.SetOperation)))
    text = f"Returns {_output_names(tree)}"
    if tables:
        text += f" from {_join_words(tables)}"
    extras: list[str] = []
    if nested:
        extras.append(
            f"computed with {nested} {'nested query' if nested == 1 else 'nested queries'}"
        )
    if combined:
        extras.append(f"combining {combined + 1} query results")
    if any(True for _ in tree.find_all(exp.Window)):
        extras.append("window functions")
    if extras:
        text += ", " + ", ".join(extras)
    tail = _limit_text(tree)
    if tail:
        text += ", " + ", ".join(tail)
    return text


def _render(tree: exp.Expression) -> str:
    if isinstance(tree, exp.Select) and _simple(tree):
        return _render_select(tree)
    return _render_fallback(tree)


__all__ = [
    "MARK",
    "SHORTENED",
    "STATEMENT_CAP",
    "VOCABULARY",
    "DescribeError",
    "cap_statement",
    "statement",
]
