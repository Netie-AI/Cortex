"""CX-MT-01 — multi-turn refinement of the previous answer's SQL.

"and by location?" regroups the prior query, "only SKU-BETA" filters it and
"only 2026" narrows it to one year. The rewrite is pure sqlglot: this module
never opens a database and never imports a pack. The caller resolves the
table's columns and nominal values through its own read path and runs the
rewritten SQL through the same guarded, manifest-enforced execute.

Detection is anchored to the whole question so a fresh question such as
"sales by region last month" is never mistaken for a refinement.

Anything that does not resolve to exactly one column or one stored value
raises ``RefineAbstain``: a filter that matches nothing, or matches the wrong
thing, is worse than saying so.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

__all__ = [
    "RefineAbstain",
    "Refinement",
    "apply_refinement",
    "candidate_like_pattern",
    "describe",
    "detect_refinement",
    "prior_table",
    "refine",
    "resolve_date_column",
    "resolve_dimension",
    "resolve_value",
    "support_probe_sql",
]


class RefineAbstain(ValueError):
    """The refinement cannot be applied without guessing."""


@dataclass(frozen=True, slots=True)
class Refinement:
    kind: str  # "regroup" | "filter" | "time"
    term: str  # dimension, value or year as the user wrote it
    # Filled in by apply_refinement so the caller can name what it did.
    column: str | None = None
    value: str | None = None


# ── detection ────────────────────────────────────────────────────────────────
_LEAD = r"(?:(?:and|now|what about|ok|okay|so)\s+)?"
_TAIL = r"(?:\s+(?:instead|please))?\s*[?.!]*"

_REGROUP = re.compile(
    rf"^{_LEAD}(?:split by|broken down by|break(?:\s+it)? down by|by|per)\s+"
    rf"(?P<dim>[a-z][a-z0-9_]*(?:\s+[a-z][a-z0-9_]*){{0,2}}){_TAIL}$"
)
_TIME = re.compile(
    rf"^{_LEAD}(?:only|just|in|for)\s+(?:the\s+year\s+)?(?P<year>(?:19|20)\d\d)"
    rf"(?:\s+only)?{_TAIL}$"
)
_VALUE = r"(?P<value>[a-z0-9][a-z0-9_\-./]*(?:\s+[a-z0-9][a-z0-9_\-./]*){0,2})"
_FILTER = re.compile(
    rf"^(?:and only|now only|and just|same but for|what about|only|just|and)\s+{_VALUE}"
    rf"(?:\s+only)?{_TAIL}$"
)
_FOR = re.compile(rf"^(?:and\s+)?for\s+{_VALUE}(?:\s+only)?{_TAIL}$")

# Words that make a "filter" match really an anaphora or a new question.
_NOT_A_VALUE = frozenset(
    {
        "them", "those", "these", "it", "that", "this", "all", "each", "every",
        "the", "a", "an", "average", "avg", "mean", "sum", "total", "count",
        "how", "what", "which", "who", "why", "when", "where", "show", "list",
        "top", "bottom", "number", "more", "less", "last", "next", "previous",
        "by", "per",
    }
)


def detect_refinement(question: str) -> Refinement | None:
    """Recognise a refinement of the previous turn, or return None."""
    q = re.sub(r"\s+", " ", (question or "").strip().lower())
    if not q:
        return None
    m = _REGROUP.match(q)
    if m:
        return Refinement("regroup", m.group("dim").strip())
    m = _TIME.match(q)
    if m:
        return Refinement("time", m.group("year"))
    m = _FILTER.match(q) or _FOR.match(q)
    if m:
        value = m.group("value").strip()
        words = value.split()
        if any(w in _NOT_A_VALUE for w in words):
            return None
        return Refinement("filter", _original_case(question, value))
    return None


def _original_case(question: str, lowered: str) -> str:
    idx = question.lower().find(lowered)
    return question[idx: idx + len(lowered)] if idx >= 0 else lowered


def describe(ref: Refinement) -> str:
    """Plain-language name of an applied refinement (for assumptions)."""
    if ref.kind == "regroup":
        return f"regroup by {ref.column or ref.term}"
    if ref.kind == "filter":
        return f"filter {ref.column or '?'} = {ref.value or ref.term}"
    return f"filter {ref.column or 'date'} to year {ref.term}"


# ── SQL shape helpers ────────────────────────────────────────────────────────
def _parse(prior_sql: str) -> exp.Select:
    try:
        tree = sqlglot.parse_one(prior_sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise RefineAbstain(f"the previous query could not be parsed ({exc})") from exc
    if not isinstance(tree, exp.Select):
        raise RefineAbstain("the previous query is not a single SELECT")
    return tree


def _single_table(tree: exp.Select) -> exp.Table:
    from_ = tree.args.get("from_") or tree.args.get("from")
    if from_ is None or tree.args.get("joins"):
        raise RefineAbstain("the previous query does not read a single table")
    table = from_.this
    if not isinstance(table, exp.Table) or not table.name:
        raise RefineAbstain("the previous query does not read a single table")
    if any(True for _ in tree.find_all(exp.Subquery)) or tree.find(exp.With):
        raise RefineAbstain("the previous query does not read a single table")
    return table


def prior_table(prior_sql: str) -> str:
    """Name of the single FROM table of ``prior_sql`` (lower case)."""
    return _single_table(_parse(prior_sql)).name.lower()


def _is_aggregate_select(tree: exp.Select) -> bool:
    return any(
        e.find(exp.AggFunc) is not None for e in tree.expressions
    )


def _col_name(node: exp.Expression) -> str | None:
    inner = node.this if isinstance(node, exp.Alias) else node
    if isinstance(inner, exp.Column):
        return inner.name.lower()
    return None


# ── regroup ──────────────────────────────────────────────────────────────────
def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ses", "xes")) and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _plural(word: str) -> str:
    if word.endswith("y") and len(word) > 2 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x")):
        return word + "es"
    return word + "s"


_SUFFIX_PREFERENCE = ("_code", "_name", "_id")


def resolve_dimension(term: str, columns: Sequence[str]) -> str:
    cols = [c.lower() for c in columns]
    key = re.sub(r"[\s\-]+", "_", term.strip().lower())
    if key in cols:
        return key
    for alt in (_singular(key), _plural(key)):
        if alt in cols:
            return alt
    stems = {key, _singular(key)}
    prefixed = sorted(
        {c for c in cols for s in stems if c.startswith(s + "_")}
    )
    if len(prefixed) == 1:
        return prefixed[0]
    if len(prefixed) > 1:
        for suffix in _SUFFIX_PREFERENCE:
            tier = [c for c in prefixed if c.endswith(suffix)]
            if len(tier) == 1:
                return tier[0]
        raise RefineAbstain(
            f"'{term}' could mean more than one column ({', '.join(prefixed)})"
        )
    raise RefineAbstain(f"'{term}' is not a column of the previous query's table")


def _regroup(tree: exp.Select, column: str) -> exp.Select:
    if not _is_aggregate_select(tree):
        raise RefineAbstain("the previous answer is a listing, not a total to regroup")
    group = tree.args.get("group")
    new_col = exp.column(column)
    if group is None or not group.expressions:
        # Ungrouped aggregate: add the dimension in front and group by it.
        tree.set("expressions", [new_col, *tree.expressions])
        tree.set("group", exp.Group(expressions=[new_col.copy()]))
        return tree
    if len(group.expressions) != 1:
        raise RefineAbstain("the previous query groups by more than one dimension")
    old = group.expressions[0]
    if not isinstance(old, exp.Column):
        raise RefineAbstain("the previous query groups by an expression, not a column")
    old_name = old.name.lower()
    projection = list(tree.expressions)
    hit = [i for i, e in enumerate(projection) if _col_name(e) == old_name]
    if len(hit) != 1:
        raise RefineAbstain("the previous query's grouped column is not projected once")
    projection[hit[0]] = new_col
    tree.set("expressions", projection)
    tree.set("group", exp.Group(expressions=[new_col.copy()]))
    order = tree.args.get("order")
    if order is not None:
        kept = [
            o for o in order.expressions
            if not any(c.name.lower() == old_name for c in o.find_all(exp.Column))
        ]
        tree.set("order", exp.Order(expressions=kept) if kept else None)
    return tree


# ── filter ───────────────────────────────────────────────────────────────────
def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text).strip().casefold())


def _strip_zeros(code: str) -> str:
    return "-".join(
        seg.lstrip("0") or "0" if seg.isdigit() else seg
        for seg in re.split(r"[-_]", code)
    )


def _affix_match(stored: str, token: str) -> bool:
    for sep in ("-", "_"):
        if stored.endswith(sep + token) or stored.startswith(token + sep):
            return True
    return False


def resolve_value(
    token: str,
    value_index: Mapping[str, Iterable[str]],
    *,
    aliases: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[str, str]:
    """Resolve a user token to exactly one ``(column, stored_value)``.

    Tiers, first unique hit wins: exact (case/whitespace-insensitive), declared
    alias (location dual-coding), prefix/suffix code encoding (BETA → SKU-BETA),
    zero-padding (LOC-1 → LOC-001). More than one hit in a tier is ambiguous.
    """
    tok = _compact(token)
    if not tok:
        raise RefineAbstain("no value to filter on")

    def _hits(pred) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for col, values in value_index.items():
            for v in values:
                if isinstance(v, str) and pred(_compact(v)):
                    out.add((col, v))
        return out

    alias_hits: set[tuple[str, str]] = set()
    for col, table in (aliases or {}).items():
        for alias, stored in table.items():
            if _compact(alias) == tok:
                alias_hits.add((col, stored))

    tiers = (
        _hits(lambda s: s == tok),
        alias_hits,
        _hits(lambda s: _affix_match(s, tok)),
        _hits(lambda s: _strip_zeros(s) == _strip_zeros(tok) or _affix_match(
            _strip_zeros(s), _strip_zeros(tok))),
    )
    for tier in tiers:
        if len(tier) == 1:
            return next(iter(tier))
        if len(tier) > 1:
            named = ", ".join(sorted(f"{c}={v}" for c, v in tier)[:5])
            raise RefineAbstain(f"'{token}' matches more than one stored value ({named})")
    raise RefineAbstain(f"'{token}' does not match any stored value")


def candidate_like_pattern(token: str) -> str | None:
    """A lower-case LIKE pattern that over-approximates ``resolve_value`` hits.

    Used by callers to fetch only candidate distinct values. None when the
    token has characters that cannot be embedded safely.
    """
    tok = _compact(token)
    if not tok or not re.fullmatch(r"[a-z0-9_\-./]+", tok):
        return None
    core = tok.lstrip("0") if tok.isdigit() else tok
    core = core or "0"
    return f"%{core}%"


def _and_where(tree: exp.Select, predicate: exp.Expression) -> exp.Select:
    where = tree.args.get("where")
    cond = predicate if where is None else exp.and_(where.this, predicate)
    tree.set("where", exp.Where(this=cond))
    return tree


# ── time ─────────────────────────────────────────────────────────────────────
_DATE_PREFERRED = ("timestamp", "txn_date", "order_date", "created_at", "date")
_DATE_EXCLUDED = re.compile(r"expir|due|resolved|audit", re.I)


def resolve_date_column(columns: Sequence[str]) -> str:
    cols = [c.lower() for c in columns]
    preferred = [c for c in _DATE_PREFERRED if c in cols]
    if len(preferred) == 1:
        return preferred[0]
    dated = [
        c for c in cols
        if re.search(r"(_date|_at|_time)$", c) and not _DATE_EXCLUDED.search(c)
    ]
    if len(dated) == 1:
        return dated[0]
    if not dated:
        raise RefineAbstain("the previous query's table has no date column to filter by year")
    raise RefineAbstain(
        f"the previous query's table has more than one date column ({', '.join(dated)})"
    )


def support_probe_sql(sql: str) -> str:
    """``SELECT COUNT(*)`` over the FROM + WHERE of a refined query.

    Tells "the filter selected nothing" apart from "the total is zero".
    """
    tree = _parse(sql)
    table = _single_table(tree)
    probe = exp.select(
        exp.alias_(exp.Count(this=exp.Star()), "matched_rows")
    ).from_(table.copy())
    where = tree.args.get("where")
    if where is not None:
        probe.set("where", where.copy())
    return probe.sql(dialect="duckdb")


# ── public rewrite ───────────────────────────────────────────────────────────
def apply_refinement(
    prior_sql: str,
    ref: Refinement,
    *,
    columns: Sequence[str],
    value_index: Mapping[str, Iterable[str]] | None = None,
    aliases: Mapping[str, Mapping[str, str]] | None = None,
) -> str:
    """Rewrite ``prior_sql`` for ``ref`` and return the new SQL."""
    sql, _ = refine(
        prior_sql, ref, columns=columns, value_index=value_index, aliases=aliases
    )
    return sql


def refine(
    prior_sql: str,
    ref: Refinement,
    *,
    columns: Sequence[str],
    value_index: Mapping[str, Iterable[str]] | None = None,
    aliases: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[str, Refinement]:
    """Rewrite ``prior_sql`` for ``ref``. Returns (sql, resolved refinement).

    ``columns`` are the prior query's FROM-table columns; ``value_index`` maps
    each nominal column to its (candidate) distinct stored values.
    """
    tree = _parse(prior_sql)
    _single_table(tree)
    if ref.kind == "regroup":
        column = resolve_dimension(ref.term, columns)
        tree = _regroup(tree, column)
        resolved = Refinement(ref.kind, ref.term, column=column)
    elif ref.kind == "filter":
        column, value = resolve_value(ref.term, value_index or {}, aliases=aliases)
        if column not in {c.lower() for c in columns}:
            raise RefineAbstain(f"'{column}' is not a column of the previous query's table")
        tree = _and_where(tree, exp.EQ(this=exp.column(column), expression=exp.Literal.string(value)))
        resolved = Refinement(ref.kind, ref.term, column=column, value=value)
    elif ref.kind == "time":
        column = resolve_date_column(columns)
        year = int(ref.term)
        pred = exp.and_(
            exp.GTE(this=exp.column(column), expression=exp.Literal.string(f"{year}-01-01")),
            exp.LT(this=exp.column(column), expression=exp.Literal.string(f"{year + 1}-01-01")),
        )
        tree = _and_where(tree, pred)
        resolved = Refinement(ref.kind, ref.term, column=column, value=str(year))
    else:  # pragma: no cover — dataclass is only built here
        raise RefineAbstain(f"unknown refinement {ref.kind!r}")
    return tree.sql(dialect="duckdb"), resolved
