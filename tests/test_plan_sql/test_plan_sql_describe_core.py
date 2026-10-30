"""PICK-READING-01 core grammar: a statement is written only for SQL it states exactly.

A picked reading is served with ``reading: <statement>`` as its first
assumption, so a statement that says anything the SQL does not do is a false
claim on a served answer (CLAUDE.md section 9, WRONG=0). Three review rounds
kept finding constructs the old "describe almost anything" renderer
misdescribed, so ``plan_sql.describe`` now writes a statement only for a small
core grammar (its module docstring) and refuses everything else; a refused
reading is simply not offered and the user still gets the normal reconfirm.

This file checks the statements against DuckDB without trusting describe:

* :func:`compile_statement` reads a statement back into SQL using only the
  statement's words (an independent reader of the English, not of describe's
  tree). Every literal is kept for that read (``keep_literal`` accepts all), so
  the statement alone determines the query. If the statement dropped or
  misstated anything, the read-back SQL returns different rows on the scratch
  tables below.
* Each construct of the grammar: its exact statement, the read-back SQL returns
  the query's rows, and the construct's nearest inverse has a different
  statement and different rows (so the check can tell them apart).
* A property over random queries generated from the grammar: each either
  refuses or renders a statement whose read-back SQL returns the same rows
  (ordered, where the query fixes the order), whose redacted form matches the
  kept form with values replaced by the mark, and which changes when one
  attribute of the query is flipped. Random queries outside the grammar always
  refuse.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any

import pytest
import sqlglot
from sqlglot import exp

from CortexOS.plan_sql import describe

duckdb = pytest.importorskip("duckdb")

MARK = describe.MARK
# Each granted column with its payload type: describe reads a bare column as a
# condition only when its type is BOOLEAN.
GRANTED: dict[str, dict[str, str]] = {
    "t": {
        "k": "VARCHAR",
        "a": "INTEGER",
        "b": "INTEGER",
        "c": "VARCHAR",
        "d": "DATE",
        "flag": "BOOLEAN",
    },
    "u": {"k": "VARCHAR", "a": "INTEGER", "c": "VARCHAR", "flag": "BOOLEAN"},
    "s": {"k": "VARCHAR", "e": "INTEGER"},
}
T_ROWS = (
    "('x', 1, 60, 'SKU-1', DATE '2024-01-05', TRUE), "
    "('x', 2, NULL, 'SKU-2', DATE '2024-02-10', FALSE), "
    "('y', 3, 50, 'OTHER', NULL, TRUE), "
    "('z', NULL, 90, NULL, DATE '2024-03-15', NULL), "
    "('w', 5, 70, 'sku-3', DATE '2024-01-20', FALSE), "
    "('y', 6, 50, 'SKU-1', DATE '2024-02-28', TRUE), "
    "('v', 7, 10, 'other', DATE '2023-12-31', TRUE), "
    "(NULL, 8, 20, 'SKU-4', DATE '2024-03-01', FALSE)"
)
U_ROWS = (
    "('x', 1, 'SKU-1', TRUE), ('y', 3, 'OTHER', FALSE), ('q', 9, 'SKU-9', TRUE), "
    "('x', 1, 'SKU-1', TRUE), ('w', NULL, NULL, NULL)"
)
S_ROWS = "('x', 10), ('y', 20), ('z', NULL), ('p', 40)"


def _never(_text: str) -> bool:
    return False


def _always(_text: str) -> bool:
    return True


def _say(sql: str) -> str:
    return describe.statement(sql, keep_literal=_never, granted=GRANTED)


def _say_kept(sql: str) -> str:
    return describe.statement(sql, keep_literal=_always, granted=GRANTED)


@pytest.fixture(scope="module")
def con() -> Iterator[Any]:
    connection = duckdb.connect()
    connection.execute("SET threads TO 1")
    connection.execute(
        "CREATE TABLE t (k VARCHAR, a INTEGER, b INTEGER, c VARCHAR, d DATE, flag BOOLEAN)"
    )
    connection.execute(f"INSERT INTO t VALUES {T_ROWS}")
    connection.execute("CREATE TABLE u (k VARCHAR, a INTEGER, c VARCHAR, flag BOOLEAN)")
    connection.execute(f"INSERT INTO u VALUES {U_ROWS}")
    connection.execute("CREATE TABLE s (k VARCHAR, e INTEGER)")
    connection.execute(f"INSERT INTO s VALUES {S_ROWS}")
    yield connection
    connection.close()


def _rows(con: Any, sql: str) -> list[tuple[Any, ...]] | str:
    try:
        return [tuple(row) for row in con.execute(sql).fetchall()]
    except duckdb.Error as exc:
        return f"error: {type(exc).__name__}"


def _bag(rows: list[tuple[Any, ...]] | str) -> list[tuple[str, ...]] | str:
    if isinstance(rows, str):
        return rows
    return sorted(tuple(repr(v) for v in row) for row in rows)


# -- reading a statement back into SQL ---------------------------------------------------


def _depths(text: str) -> list[int]:
    """Per character: its parenthesis depth, or -1 inside a quoted name or literal."""
    out: list[int] = []
    depth = 0
    quote = ""
    for char in text:
        if quote:
            out.append(-1)
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
            out.append(-1)
            continue
        if char == ")":
            depth -= 1
        out.append(depth)
        if char == "(":
            depth += 1
    return out


def _tops(text: str, sep: str) -> list[int]:
    depths = _depths(text)
    found: list[int] = []
    i = text.find(sep)
    while i != -1:
        if all(d == 0 for d in depths[i : i + len(sep)]):
            found.append(i)
            i = text.find(sep, i + len(sep))
        else:
            i = text.find(sep, i + 1)
    return found


def _find_top(text: str, sep: str) -> int:
    found = _tops(text, sep)
    return found[0] if found else -1


def _split_top(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for i in _tops(text, sep):
        parts.append(text[start:i])
        start = i + len(sep)
    parts.append(text[start:])
    return parts


def _closes_at_end(text: str, open_at: int) -> bool:
    """Whether the parenthesis at ``open_at`` closes at the last character."""
    depth = 0
    quote = ""
    for i in range(open_at, len(text)):
        char = text[i]
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i == len(text) - 1
    return False


_AGGREGATE_WORDS = (
    ("number of distinct ", "COUNT(DISTINCT {})"),
    ("number of non-empty ", "COUNT({})"),
    ("total of ", "SUM({})"),
    ("average of ", "AVG({})"),
    ("smallest ", "MIN({})"),
    ("largest ", "MAX({})"),
)
_ATOMS = (
    (" does not match, ignoring case, ", "NOT ILIKE"),
    (" matches, ignoring case, ", "ILIKE"),
    (" does not match ", "NOT LIKE"),
    (" matches ", "LIKE"),
    (" is more than ", ">"),
    (" is at least ", ">="),
    (" is less than ", "<"),
    (" is at most ", "<="),
    (" is not ", "<>"),
    (" is ", "="),
)
_VALUE = re.compile(r"^(?:'|-?\d|CAST\(|TRUE$|FALSE$)")


def _name(text: str) -> str:
    """A printed name as a SQL identifier: quoted names keep their quotes."""
    if text.startswith('"'):
        assert text.endswith('"') and len(text) > 1, text
        return text
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text), f"not a name: {text!r}"
    return f'"{text}"'


def _column(text: str) -> str:
    parts = _split_top(text, ".")
    assert 1 <= len(parts) <= 2, text
    return ".".join(_name(part) for part in parts)


class _Reader:
    """Reads one statement back into SQL from its words alone."""

    def __init__(self) -> None:
        self.cases: dict[int, str] = {}

    def operand(self, text: str) -> str:
        assert text != MARK, "a kept statement shows no mark"
        if text == "number of rows":
            return "COUNT(*)"
        for words, template in _AGGREGATE_WORDS:
            if text.startswith(words):
                return template.format(_column(text[len(words) :]))
        reference = re.fullmatch(r"computed value (\d+)", text)
        if reference:
            assert int(reference.group(1)) in self.cases, text
            return f"cv{reference.group(1)}"
        if _VALUE.match(text):
            return text
        return _column(text)

    def condition(self, text: str) -> str:
        ors = _split_top(text, " or ")
        ands = self._between(_split_top(text, " and "))
        assert not (len(ors) > 1 and len(ands) > 1), f"grouping not shown: {text!r}"
        if len(ors) > 1:
            return "(" + " OR ".join(self.term(p) for p in ors) + ")"
        if len(ands) > 1:
            return "(" + " AND ".join(self.term(p) for p in ands) + ")"
        return self.term(text)

    @staticmethod
    def _between(pieces: list[str]) -> list[str]:
        """Rejoin ``x is between L and H``: its ``and`` is not a connective."""
        out: list[str] = []
        i = 0
        while i < len(pieces):
            piece = pieces[i]
            for _ in _tops(piece, " is between "):
                i += 1
                piece = f"{piece} and {pieces[i]}"
            out.append(piece)
            i += 1
        return out

    def term(self, text: str) -> str:
        if text.startswith("(") and _closes_at_end(text, 0):
            return "(" + self.condition(text[1:-1]) + ")"
        if text.startswith("not (") and _closes_at_end(text, 4):
            return "NOT (" + self.condition(text[5:-1]) + ")"
        return self.atom(text)

    def atom(self, text: str) -> str:
        if text.endswith(" is not empty"):
            return f"{self.operand(text[: -len(' is not empty')])} IS NOT NULL"
        if text.endswith(" is empty"):
            return f"{self.operand(text[: -len(' is empty')])} IS NULL"
        i = _find_top(text, " is one of (")
        if i != -1:
            assert text.endswith(")"), text
            values = _split_top(text[i + len(" is one of (") : -1], ", ")
            return f"{self.operand(text[:i])} IN ({', '.join(self.operand(v) for v in values)})"
        i = _find_top(text, " is between ")
        if i != -1:
            low, high = _split_top(text[i + len(" is between ") :], " and ")
            return f"{self.operand(text[:i])} BETWEEN {self.operand(low)} AND {self.operand(high)}"
        hits = [(at, phrase, op) for phrase, op in _ATOMS for at in _tops(text, phrase)[:1]]
        if hits:
            # The left operand holds no phrase: the first one starts the atom, and
            # at one position the longest phrase is the one meant.
            at = min(hit[0] for hit in hits)
            _at, phrase, op = max((h for h in hits if h[0] == at), key=lambda h: len(h[1]))
            left, right = text[:at], text[at + len(phrase) :]
            return f"{self.operand(left)} {op} {self.operand(right)}"
        return self.operand(text)  # a boolean column, read as itself

    def case(self, text: str) -> str:
        cut = _tops(text, ", otherwise ")
        assert len(cut) == 1, text
        branches, default = text[: cut[0]], text[cut[0] + len(", otherwise ") :]
        assert branches.startswith("when "), text
        sql = "CASE"
        for branch in _split_top(branches[len("when ") :], ", when "):
            at = _tops(branch, " then ")[-1]
            sql += f" WHEN {self.condition(branch[:at])} THEN {self.operand(branch[at + 6 :])}"
        if default != "empty":
            sql += f" ELSE {self.operand(default)}"
        return sql + " END"

    def output(self, text: str, position: int) -> str:
        computed = re.fullmatch(r"computed value (\d+) \((.*)\)", text, re.S)
        if computed:
            assert int(computed.group(1)) == position, text
            self.cases[position] = self.case(computed.group(2))
            return f"{self.cases[position]} AS cv{position}"
        if text == "all columns":
            return "*"
        if text.startswith("all columns of "):
            return _name(text[len("all columns of ") :]) + ".*"
        return self.operand(text)

    def key(self, text: str) -> str:
        reference = re.fullmatch(r"computed value (\d+)", text)
        if reference:
            return reference.group(1)
        return self.operand(text)

    def source(self, text: str) -> str:
        chunks = _split_top(text, ", ")
        first = chunks[0]
        joins: list[list[str]] = []
        for chunk in chunks[1:]:
            if chunk.startswith("matched to "):
                rest = chunk[len("matched to ") :]
                at = _find_top(rest, " on ")
                joins.append(["JOIN", _name(rest[:at]), self.condition(rest[at + 4 :])])
            elif chunk == f"keeping rows of {first} with no match":
                assert joins and joins[-1][0] == "JOIN", text
                joins[-1][0] = "LEFT JOIN"
            elif chunk.startswith(
                ("keeping only rows with some match in ", "keeping only rows with no match in ")
            ):
                kind = "SEMI JOIN" if " some match " in chunk else "ANTI JOIN"
                rest = chunk.split(" match in ", 1)[1]
                at = _find_top(rest, " on ")
                joins.append([kind, _name(rest[:at]), self.condition(rest[at + 4 :])])
            else:
                raise AssertionError(f"not a source phrase: {chunk!r}")
        return _name(first) + "".join(f" {k} {t} ON {c}" for k, t, c in joins)


def compile_statement(text: str) -> str:
    """The SQL a kept statement says, from its words alone."""
    assert text.startswith("Returns "), text
    assert not text.endswith(describe.SHORTENED), "a shortened statement says less"
    body = text[len("Returns ") :]
    limit = ""
    tail = re.search(r", first (\d+) rows?$", body)
    if tail:
        limit = f" LIMIT {tail.group(1)}"
        body = body[: tail.start()]
    order = ""
    at = _find_top(body, ", sorted by ")
    reader = _Reader()
    order_text = body[at + len(", sorted by ") :] if at != -1 else ""
    if at != -1:
        body = body[:at]
    at = _find_top(body, " from ")
    head, rest = body[:at], body[at + len(" from ") :]
    distinct = head.startswith("distinct ")
    if distinct:
        head = head[len("distinct ") :]
    group_text = ""
    at = _find_top(head, " for each ")
    if at != -1:
        head, group_text = head[:at], head[at + len(" for each ") :]
    having_text = where_text = ""
    at = _find_top(rest, " having ")
    if at != -1:
        rest, having_text = rest[:at], rest[at + len(" having ") :]
    at = _find_top(rest, " where ")
    if at != -1:
        rest, where_text = rest[:at], rest[at + len(" where ") :]
    items = [i for chunk in _split_top(head, ", ") for i in _split_top(chunk, " and ")]
    outputs = [reader.output(item, n) for n, item in enumerate(items, 1)]
    sql = f"SELECT {'DISTINCT ' if distinct else ''}{', '.join(outputs)} FROM {reader.source(rest)}"
    if where_text:
        sql += f" WHERE {reader.condition(where_text)}"
    if group_text:
        keys = [i for chunk in _split_top(group_text, ", ") for i in _split_top(chunk, " and ")]
        sql += " GROUP BY " + ", ".join(reader.key(k) for k in keys)
    if having_text:
        sql += f" HAVING {reader.condition(having_text)}"
    if order_text:
        keys = []
        for key in _split_top(order_text, ", "):
            nulls = ""
            if key.endswith(" with empty values first"):
                key, nulls = key[: -len(" with empty values first")], " NULLS FIRST"
            direction = ""
            if key.endswith(" descending"):
                key, direction = key[: -len(" descending")], " DESC"
            keys.append(f"{reader.key(key)}{direction or ' ASC'}{nulls or ' NULLS LAST'}")
        order = " ORDER BY " + ", ".join(keys)
    return sql + order + limit


def _redacted_matches_kept(redacted: str, kept: str) -> bool:
    """The redacted statement is the kept one with each shown value replaced by the mark."""
    pattern = "(.+?)".join(re.escape(piece) for piece in redacted.split(MARK))
    return re.fullmatch(pattern, kept, re.S) is not None


def _check_claims(con: Any, sql: str, ordered: bool) -> str:
    """Render ``sql`` and check its statement on DuckDB. Returns the redacted statement."""
    redacted = _say(sql)
    kept = _say_kept(sql)
    assert _redacted_matches_kept(redacted, kept), (redacted, kept)
    compiled = compile_statement(kept)
    rows, back = _rows(con, sql), _rows(con, compiled)
    assert isinstance(rows, list), (sql, rows)
    view: Callable[[Any], Any] = (lambda r: r) if ordered else _bag
    assert view(back) == view(rows), (sql, kept, compiled)
    # The read-back SQL says the same thing: its statement is the query's.
    assert _say(compiled) == redacted, (compiled, redacted)
    return redacted


# -- 1. every construct of the core grammar, checked on DuckDB ------------------------------


@dataclass(frozen=True)
class Construct:
    sql: str
    statement: str
    inverse: str  # the nearest other reading: a different statement and different rows
    ordered: bool = False  # whether the row order is part of the claim


M = MARK
CONSTRUCTS: dict[str, Construct] = {
    # outputs
    "column": Construct("SELECT k FROM t", "Returns k from t", "SELECT c FROM t"),
    "qualified_column": Construct("SELECT t.k FROM t", "Returns t.k from t", "SELECT t.c FROM t"),
    "distinct": Construct(
        "SELECT DISTINCT k FROM t", "Returns distinct k from t", "SELECT k FROM t"
    ),
    "star": Construct("SELECT * FROM t", "Returns all columns from t", "SELECT k FROM t"),
    "qualified_star": Construct(
        "SELECT u.* FROM t JOIN u ON t.k = u.k",
        "Returns all columns of u from t, matched to u on t.k is u.k",
        "SELECT t.* FROM t JOIN u ON t.k = u.k",
    ),
    "count_star": Construct(
        "SELECT COUNT(*) FROM t", "Returns number of rows from t", "SELECT COUNT(a) FROM t"
    ),
    "count_one": Construct(
        "SELECT COUNT(1) FROM t", "Returns number of rows from t", "SELECT COUNT(a) FROM t"
    ),
    "count_column": Construct(
        "SELECT COUNT(b) FROM t",
        "Returns number of non-empty b from t",
        "SELECT COUNT(DISTINCT b) FROM t",
    ),
    "count_distinct": Construct(
        "SELECT COUNT(DISTINCT k) FROM t",
        "Returns number of distinct k from t",
        "SELECT COUNT(k) FROM t",
    ),
    "sum": Construct("SELECT SUM(b) FROM t", "Returns total of b from t", "SELECT AVG(b) FROM t"),
    "avg": Construct("SELECT AVG(b) FROM t", "Returns average of b from t", "SELECT SUM(b) FROM t"),
    "min": Construct("SELECT MIN(c) FROM t", "Returns smallest c from t", "SELECT MAX(c) FROM t"),
    "max": Construct("SELECT MAX(d) FROM t", "Returns largest d from t", "SELECT MIN(d) FROM t"),
    "case_with_else": Construct(
        "SELECT CASE WHEN a > 3 THEN 'big' ELSE 'small' END AS size FROM t",
        f"Returns computed value 1 (when a is more than {M} then {M}, otherwise {M}) from t",
        "SELECT CASE WHEN a <= 3 THEN 'big' ELSE 'small' END AS size FROM t",
    ),
    "case_without_else": Construct(
        "SELECT CASE WHEN a > 3 THEN 'big' WHEN b < 50 THEN 'low' END FROM t",
        f"Returns computed value 1 (when a is more than {M} then {M}, "
        f"when b is less than {M} then {M}, otherwise empty) from t",
        "SELECT CASE WHEN a > 3 THEN 'big' WHEN b < 50 THEN 'low' ELSE 'small' END FROM t",
    ),
    # conditions
    "eq": Construct(
        "SELECT k FROM t WHERE c = 'SKU-1'",
        f"Returns k from t where c is {M}",
        "SELECT k FROM t WHERE c <> 'SKU-1'",
    ),
    "neq": Construct(
        "SELECT k FROM t WHERE c <> 'SKU-1'",
        f"Returns k from t where c is not {M}",
        "SELECT k FROM t WHERE c = 'SKU-1'",
    ),
    "gt": Construct(
        "SELECT k FROM t WHERE b > 50",
        f"Returns k from t where b is more than {M}",
        "SELECT k FROM t WHERE b <= 50",
    ),
    "gte": Construct(
        "SELECT k FROM t WHERE b >= 50",
        f"Returns k from t where b is at least {M}",
        "SELECT k FROM t WHERE b < 50",
    ),
    "lt": Construct(
        "SELECT k FROM t WHERE b < 50",
        f"Returns k from t where b is less than {M}",
        "SELECT k FROM t WHERE b >= 50",
    ),
    "lte": Construct(
        "SELECT k FROM t WHERE b <= 50",
        f"Returns k from t where b is at most {M}",
        "SELECT k FROM t WHERE b > 50",
    ),
    "value_on_the_left": Construct(
        "SELECT k FROM t WHERE 50 < b",
        f"Returns k from t where {M} is less than b",
        "SELECT k FROM t WHERE 50 > b",
    ),
    "column_to_column": Construct(
        "SELECT k FROM t WHERE a < b",
        "Returns k from t where a is less than b",
        "SELECT k FROM t WHERE a > b",
    ),
    "like": Construct(
        "SELECT k FROM t WHERE c LIKE 'SKU%'",
        f"Returns k from t where c matches {M}",
        "SELECT k FROM t WHERE c NOT LIKE 'SKU%'",
    ),
    "not_like": Construct(
        "SELECT k FROM t WHERE c NOT LIKE 'SKU%'",
        f"Returns k from t where c does not match {M}",
        "SELECT k FROM t WHERE c LIKE 'SKU%'",
    ),
    "ilike": Construct(
        "SELECT k FROM t WHERE c ILIKE 'sku%'",
        f"Returns k from t where c matches, ignoring case, {M}",
        "SELECT k FROM t WHERE c LIKE 'sku%'",
    ),
    "not_ilike": Construct(
        "SELECT k FROM t WHERE c NOT ILIKE 'sku%'",
        f"Returns k from t where c does not match, ignoring case, {M}",
        "SELECT k FROM t WHERE c ILIKE 'sku%'",
    ),
    "in": Construct(
        "SELECT k FROM t WHERE a IN (1, 3, 5)",
        f"Returns k from t where a is one of ({M}, {M}, {M})",
        "SELECT k FROM t WHERE a NOT IN (1, 3, 5)",
    ),
    "not_in": Construct(
        "SELECT k FROM t WHERE a NOT IN (1, 3, 5)",
        f"Returns k from t where not (a is one of ({M}, {M}, {M}))",
        "SELECT k FROM t WHERE a IN (1, 3, 5)",
    ),
    "between": Construct(
        "SELECT k FROM t WHERE b BETWEEN 20 AND 60",
        f"Returns k from t where b is between {M} and {M}",
        "SELECT k FROM t WHERE b NOT BETWEEN 20 AND 60",
    ),
    "not_between": Construct(
        "SELECT k FROM t WHERE b NOT BETWEEN 20 AND 60",
        f"Returns k from t where not (b is between {M} and {M})",
        "SELECT k FROM t WHERE b BETWEEN 20 AND 60",
    ),
    "is_null": Construct(
        "SELECT k FROM t WHERE b IS NULL",
        "Returns k from t where b is empty",
        "SELECT k FROM t WHERE b IS NOT NULL",
    ),
    "is_not_null": Construct(
        "SELECT k FROM t WHERE b IS NOT NULL",
        "Returns k from t where b is not empty",
        "SELECT k FROM t WHERE b IS NULL",
    ),
    "boolean_column": Construct(
        "SELECT k FROM t WHERE flag",
        "Returns k from t where flag",
        "SELECT k FROM t WHERE NOT flag",
    ),
    "not": Construct(
        "SELECT k FROM t WHERE NOT (b > 50 AND flag)",
        f"Returns k from t where not (b is more than {M} and flag)",
        "SELECT k FROM t WHERE b > 50 AND flag",
    ),
    "or_of_and": Construct(
        "SELECT k FROM t WHERE a > 5 OR b < 50 AND flag",
        f"Returns k from t where a is more than {M} or (b is less than {M} and flag)",
        "SELECT k FROM t WHERE (a > 5 OR b < 50) AND flag",
    ),
    "and_of_or": Construct(
        "SELECT k FROM t WHERE (a > 5 OR b < 50) AND flag",
        f"Returns k from t where (a is more than {M} or b is less than {M}) and flag",
        "SELECT k FROM t WHERE a > 5 OR b < 50 AND flag",
    ),
    "cast_value": Construct(
        "SELECT k FROM t WHERE d >= CAST('2024-02-01' AS DATE)",
        f"Returns k from t where d is at least {M}",
        "SELECT k FROM t WHERE d < CAST('2024-02-01' AS DATE)",
    ),
    "date_value": Construct(
        "SELECT k FROM t WHERE d < DATE '2024-02-01'",
        f"Returns k from t where d is less than {M}",
        "SELECT k FROM t WHERE d >= DATE '2024-02-01'",
    ),
    "negative_value": Construct(
        "SELECT k FROM t WHERE a > -1",
        f"Returns k from t where a is more than {M}",
        "SELECT k FROM t WHERE a <= -1",
    ),
    "boolean_value": Construct(
        "SELECT k FROM t WHERE flag = TRUE",
        f"Returns k from t where flag is {M}",
        "SELECT k FROM t WHERE flag <> TRUE",
    ),
    # joins
    "inner_join": Construct(
        "SELECT t.k, u.a FROM t JOIN u ON t.k = u.k",
        "Returns t.k and u.a from t, matched to u on t.k is u.k",
        "SELECT t.k, u.a FROM t LEFT JOIN u ON t.k = u.k",
    ),
    "left_join": Construct(
        "SELECT t.k, u.a FROM t LEFT JOIN u ON t.k = u.k",
        "Returns t.k and u.a from t, matched to u on t.k is u.k, keeping rows of t with no match",
        "SELECT t.k, u.a FROM t JOIN u ON t.k = u.k",
    ),
    "semi_join": Construct(
        "SELECT t.k FROM t SEMI JOIN u ON t.k = u.k",
        "Returns t.k from t, keeping only rows with some match in u on t.k is u.k",
        "SELECT t.k FROM t ANTI JOIN u ON t.k = u.k",
    ),
    "anti_join": Construct(
        "SELECT t.k FROM t ANTI JOIN u ON t.k = u.k AND u.flag",
        "Returns t.k from t, keeping only rows with no match in u on t.k is u.k and u.flag",
        "SELECT t.k FROM t SEMI JOIN u ON t.k = u.k AND u.flag",
    ),
    "aliased_join": Construct(
        "SELECT x.k, y.e FROM t AS x JOIN s AS y ON x.k = y.k AND y.e > 10",
        f"Returns t.k and s.e from t, matched to s on t.k is s.k and s.e is more than {M}",
        "SELECT x.k, y.e FROM t AS x JOIN s AS y ON x.k = y.k AND y.e <= 10",
    ),
    "three_tables": Construct(
        "SELECT t.k, u.c, s.e FROM t JOIN u ON t.k = u.k JOIN s ON u.k = s.k",
        "Returns t.k, u.c and s.e from t, matched to u on t.k is u.k, matched to s on u.k is s.k",
        "SELECT t.k, u.c, s.e FROM t JOIN u ON t.k = u.k JOIN s ON t.k <> s.k",
    ),
    "join_then_semi": Construct(
        "SELECT t.k, u.a FROM t JOIN u ON t.a = u.a SEMI JOIN s ON t.k = s.k",
        "Returns t.k and u.a from t, matched to u on t.a is u.a, "
        "keeping only rows with some match in s on t.k is s.k",
        "SELECT t.k, u.a FROM t JOIN u ON t.a = u.a ANTI JOIN s ON t.k = s.k",
    ),
    # grouping
    "group_by_column": Construct(
        "SELECT k, COUNT(*) FROM t GROUP BY k",
        "Returns k and number of rows for each k from t",
        "SELECT k, COUNT(*) FROM t GROUP BY k, a",
    ),
    "group_by_ordinal": Construct(
        "SELECT k, SUM(b) FROM t GROUP BY 1",
        "Returns k and total of b for each k from t",
        "SELECT k, MAX(b) FROM t GROUP BY 1",
    ),
    "group_by_parenthesized_ordinal": Construct(
        "SELECT flag, COUNT(*) FROM t GROUP BY (1)",
        "Returns flag and number of rows for each flag from t",
        "SELECT flag, COUNT(*) FROM t GROUP BY flag, k",
    ),
    "group_by_case_output": Construct(
        "SELECT CASE WHEN b > 50 THEN 'hi' ELSE 'lo' END AS band, COUNT(*) AS n FROM t "
        "GROUP BY CASE WHEN b > 50 THEN 'hi' ELSE 'lo' END",
        f"Returns computed value 1 (when b is more than {M} then {M}, otherwise {M}) and "
        "number of rows for each computed value 1 from t",
        "SELECT CASE WHEN b >= 50 THEN 'hi' ELSE 'lo' END AS band, COUNT(*) AS n FROM t "
        "GROUP BY CASE WHEN b >= 50 THEN 'hi' ELSE 'lo' END",
    ),
    # PICK-READING-01 round 4: "group_by_output_name" (GROUP BY band) and
    # "where_output_name" (WHERE band = ...) were here. DuckDB binds a real
    # column first in GROUP BY and WHERE, and a column the payload does not list
    # can carry the alias's name, so both refuse now (OUTPUT_NAME_REFUSES below).
    "having": Construct(
        "SELECT k, COUNT(*) FROM t GROUP BY k HAVING COUNT(*) > 1",
        f"Returns k and number of rows for each k from t having number of rows is more than {M}",
        "SELECT k, COUNT(*) FROM t GROUP BY k HAVING COUNT(*) <= 1",
    ),
    "having_grouped_column": Construct(
        "SELECT k, SUM(b) FROM t GROUP BY k HAVING k <> 'x' AND MIN(a) >= 2",
        f"Returns k and total of b for each k from t having k is not {M} and "
        f"smallest a is at least {M}",
        "SELECT k, SUM(b) FROM t GROUP BY k HAVING k <> 'x' OR MIN(a) >= 2",
    ),
    # PICK-READING-01 round 4: was ``SUM(b) AS b ... HAVING b > 60``. An output
    # named like a column the statement reads refuses now (OUTPUT_NAME_REFUSES);
    # an alias no other name could be keeps DuckDB's alias-first HAVING binding.
    "having_output_alias": Construct(
        "SELECT k, SUM(b) AS total FROM t GROUP BY k HAVING total > 60",
        f"Returns k and total of b for each k from t having total of b is more than {M}",
        "SELECT k, SUM(b) AS total FROM t GROUP BY k HAVING total <= 60",
    ),
    # sorting and limits (row order is the claim)
    "order_asc": Construct(
        "SELECT k, b FROM t ORDER BY b",
        "Returns k and b from t, sorted by b",
        "SELECT k, b FROM t ORDER BY b DESC",
        ordered=True,
    ),
    "order_desc": Construct(
        "SELECT k, b FROM t ORDER BY b DESC, k",
        "Returns k and b from t, sorted by b descending, k",
        "SELECT k, b FROM t ORDER BY b, k",
        ordered=True,
    ),
    "order_nulls_first": Construct(
        "SELECT k, b FROM t ORDER BY b NULLS FIRST, k",
        "Returns k and b from t, sorted by b with empty values first, k",
        "SELECT k, b FROM t ORDER BY b, k",
        ordered=True,
    ),
    "order_desc_nulls_first": Construct(
        "SELECT k, b FROM t ORDER BY b DESC NULLS FIRST, k",
        "Returns k and b from t, sorted by b descending with empty values first, k",
        "SELECT k, b FROM t ORDER BY b DESC, k",
        ordered=True,
    ),
    "order_explicit_nulls_last": Construct(
        "SELECT k, b FROM t ORDER BY b DESC NULLS LAST, k",
        "Returns k and b from t, sorted by b descending, k",
        "SELECT k, b FROM t ORDER BY b DESC NULLS FIRST, k",
        ordered=True,
    ),
    "order_ordinal": Construct(
        "SELECT k, a FROM t ORDER BY 2 DESC",
        "Returns k and a from t, sorted by a descending",
        "SELECT k, a FROM t ORDER BY 1 DESC",
        ordered=True,
    ),
    "order_parenthesized_ordinal": Construct(
        "SELECT k, a FROM t ORDER BY (2) DESC",
        "Returns k and a from t, sorted by a descending",
        "SELECT k, a FROM t ORDER BY 2",
        ordered=True,
    ),
    "order_output_name": Construct(
        "SELECT k, COUNT(*) AS n FROM t GROUP BY k ORDER BY n DESC, k",
        "Returns k and number of rows for each k from t, sorted by number of rows descending, k",
        "SELECT k, COUNT(*) AS n FROM t GROUP BY k ORDER BY n, k",
        ordered=True,
    ),
    "order_aggregate": Construct(
        "SELECT k FROM t GROUP BY k ORDER BY MAX(b) DESC, k",
        "Returns k for each k from t, sorted by largest b descending, k",
        "SELECT k FROM t GROUP BY k ORDER BY SUM(b) DESC, k",
        ordered=True,
    ),
    "limit": Construct(
        "SELECT a FROM t ORDER BY a LIMIT 3",
        "Returns a from t, sorted by a, first 3 rows",
        "SELECT a FROM t ORDER BY a LIMIT 4",
        ordered=True,
    ),
    "limit_leading_zeros": Construct(
        "SELECT a FROM t ORDER BY a LIMIT 007",
        "Returns a from t, sorted by a, first 7 rows",
        "SELECT a FROM t ORDER BY a LIMIT 6",
        ordered=True,
    ),
    "limit_one": Construct(
        "SELECT a FROM t ORDER BY a DESC LIMIT 1",
        "Returns a from t, sorted by a descending, first 1 row",
        "SELECT a FROM t ORDER BY a LIMIT 1",
        ordered=True,
    ),
}


@pytest.mark.parametrize("case", sorted(CONSTRUCTS))
def test_must_fail_each_core_construct_is_stated_exactly(case: str) -> None:
    assert _say(CONSTRUCTS[case].sql) == CONSTRUCTS[case].statement


@pytest.mark.parametrize("case", sorted(CONSTRUCTS))
def test_each_core_statement_matches_duckdb_and_differs_from_its_inverse(
    con: Any, case: str
) -> None:
    spec = CONSTRUCTS[case]
    statement = _check_claims(con, spec.sql, spec.ordered)
    view: Callable[[Any], Any] = (lambda r: r) if spec.ordered else _bag
    assert view(_rows(con, spec.inverse)) != view(_rows(con, spec.sql)), "no data tells them apart"
    assert _say(spec.inverse) != statement, (spec.inverse, statement)


def test_the_reader_is_not_vacuous(con: Any) -> None:
    """The read-back catches a statement that drops or flips a word: each kept
    statement with one phrase changed reads back to SQL whose rows differ."""
    edits = [
        ("not_like", " does not match ", " matches "),
        ("anti_join", " no match in ", " some match in "),
        ("left_join", ", keeping rows of t with no match", ""),
        ("order_desc", " descending", ""),
        ("order_nulls_first", " with empty values first", ""),
        ("or_of_and", "(b is less than 50 and flag)", "b is less than 50 and flag"),
        ("not_in", "not (a is one of (1, 3, 5))", "a is one of (1, 3, 5)"),
        ("distinct", "distinct ", ""),
        ("limit", "first 3 rows", "first 4 rows"),
    ]
    for case, old, new in edits:
        spec = CONSTRUCTS[case]
        kept = _say_kept(spec.sql)
        assert old in kept, (case, kept)
        changed = kept.replace(old, new)
        try:
            back = _rows(con, compile_statement(changed))
        except AssertionError:
            continue  # the reader refuses an ungrouped mix of "and" and "or"
        view: Callable[[Any], Any] = (lambda r: r) if spec.ordered else _bag
        assert view(back) != view(_rows(con, spec.sql)), (case, changed)


# -- 2. the must-fails this design closes (each rendered a false statement on 885f540) ----

# 885f540 described each of these; each statement was false. Each is outside the
# core grammar now, so it refuses (and the reading is not offered).
CLOSED: dict[str, tuple[str, str]] = {
    # "where sku is not NULL": DuckDB returns no rows for ``<> NULL``.
    "neq_null": ("SELECT k FROM t WHERE c <> NULL", "SELECT k FROM t WHERE c IS NOT NULL"),
    "not_like_or_neq_null": (
        "SELECT k FROM t WHERE c NOT LIKE 'SKU%' OR c <> NULL",
        "SELECT k FROM t WHERE c NOT LIKE 'SKU%' OR c IS NOT NULL",
    ),
    "eq_null": ("SELECT k FROM t WHERE c = NULL", "SELECT k FROM t WHERE c IS NULL"),
    "in_null": ("SELECT k FROM t WHERE a IN (1, NULL)", "SELECT k FROM t WHERE a = 1 OR a IS NULL"),
    "not_in_null": (
        "SELECT k FROM t WHERE a NOT IN (1, NULL)",
        "SELECT k FROM t WHERE a <> 1 AND a IS NOT NULL",
    ),
    "between_null": (
        "SELECT k FROM t WHERE a BETWEEN NULL AND 3",
        "SELECT k FROM t WHERE a <= 3",
    ),
    "like_null": ("SELECT k FROM t WHERE c LIKE NULL", "SELECT k FROM t WHERE c IS NULL"),
    "cast_null": (
        "SELECT k FROM t WHERE a <> CAST(NULL AS INTEGER)",
        "SELECT k FROM t WHERE a IS NOT NULL",
    ),
    # A nested star modifier: the output called "c" holds k.
    "nested_star_replace": (
        "SELECT c FROM (SELECT * REPLACE (k AS c) FROM t) AS q",
        "SELECT c FROM t",
    ),
    "nested_star_rename": (
        "SELECT c FROM (SELECT * EXCLUDE (c) RENAME (k AS c) FROM t) AS q",
        "SELECT c FROM t",
    ),
    "unpivot": (
        "SELECT k FROM (UNPIVOT t ON a, b INTO NAME n VALUE k) AS q",
        "SELECT k FROM t",
    ),
    # A self-join and a reused alias: two relation instances merged into one table.
    "self_join": (
        "SELECT x.k FROM t AS x JOIN t AS y ON x.a < y.a",
        "SELECT t.k FROM t WHERE t.a < t.a",
    ),
    "exists_reused_alias": (
        "SELECT x.k FROM t AS x WHERE EXISTS (SELECT 1 FROM u AS x WHERE x.a > 1)",
        "SELECT t.k FROM t WHERE t.a > 1",
    ),
    # The fallback dropped a top-level WHERE and QUALIFY.
    "fallback_where": (
        "SELECT k FROM (SELECT k, a FROM t) AS q WHERE a > 5",
        "SELECT k FROM t",
    ),
    "fallback_qualify": (
        "SELECT k, ROW_NUMBER() OVER (ORDER BY a) AS r FROM t QUALIFY r = 1",
        "SELECT k FROM t",
    ),
}


@pytest.mark.parametrize("case", sorted(CLOSED))
def test_must_fail_a_misdescribed_construct_refuses(con: Any, case: str) -> None:
    sql, misread = CLOSED[case]
    sqlglot.parse_one(sql, read="duckdb")
    with pytest.raises(describe.DescribeError) as refused:
        _say(sql)
    assert "unparsable" not in str(refused.value)
    rows = _rows(con, sql)
    assert isinstance(rows, list), rows
    assert _bag(rows) != _bag(_rows(con, misread)), "the old statement's reading must differ"


def test_must_fail_a_parenthesized_ordinal_is_its_output(con: Any) -> None:
    """885f540 said "for each <a value>" for ``GROUP BY (1)``: it is the ordinal."""
    sql = "SELECT k, COUNT(*) FROM t GROUP BY (1)"
    assert _say(sql) == "Returns k and number of rows for each k from t"
    assert MARK not in _say(sql)
    assert _bag(_rows(con, sql)) == _bag(_rows(con, "SELECT k, COUNT(*) FROM t GROUP BY k"))
    order = "SELECT k, a FROM t ORDER BY ((2)) DESC NULLS FIRST"
    assert _say(order) == "Returns k and a from t, sorted by a descending with empty values first"
    assert _rows(con, order) == _rows(con, "SELECT k, a FROM t ORDER BY a DESC NULLS FIRST")


# -- 2b. names describe cannot see (PICK-READING-01 round 4) -------------------------------

# The payload lists a subset of a table's columns, and DuckDB binds a name
# against all of them. Here the grant lists t without ``d`` and ``a``: both
# are real columns DuckDB reads, and describe cannot know they exist. Each case
# rendered on a8a0d2d as the reading in its third field; DuckDB returns the
# rows of the fourth (the real binding), which differ.
SUBSET: dict[str, dict[str, str]] = {
    "t": {"k": "VARCHAR", "b": "INTEGER", "c": "VARCHAR", "flag": "BOOLEAN"},
    "u": dict(GRANTED["u"]),
}


@dataclass(frozen=True)
class Unseen:
    sql: str
    reason: str  # what describe says when it refuses
    claimed: str  # SQL of the statement a8a0d2d showed
    bound: str  # SQL of what DuckDB actually binds


UNSEEN: dict[str, Unseen] = {
    # "Returns c from t where c is empty": DuckDB tests the real column d.
    "where_alias_names_an_unlisted_column": Unseen(
        "SELECT c AS d FROM t WHERE d IS NULL",
        "an output named like a column the statement reads",
        "SELECT c FROM t WHERE c IS NULL",
        "SELECT c FROM t WHERE t.d IS NULL",
    ),
    # "... for each largest c": DuckDB groups by the real column d.
    "group_alias_names_an_unlisted_column": Unseen(
        "SELECT MAX(c) AS d, COUNT(*) AS n FROM t GROUP BY d",
        "an output named like a column the statement reads",
        "SELECT MAX(c), COUNT(*) FROM t",
        "SELECT MAX(c) AS d, COUNT(*) AS n FROM t GROUP BY t.d",
    ),
    # "..., keeping only rows with some match in u on t.k is u.k where a is more
    # than ...": u is not visible after its SEMI JOIN, so DuckDB reads t.a.
    "semi_join_where_reads_the_left_table": Unseen(
        "SELECT t.k FROM t SEMI JOIN u ON t.k = u.k WHERE a > 2",
        "a name that is not a granted column",
        "SELECT k FROM t WHERE EXISTS (SELECT 1 FROM u WHERE t.k = u.k AND u.a > 2)",
        "SELECT k FROM t WHERE EXISTS (SELECT 1 FROM u WHERE t.k = u.k) AND t.a > 2",
    ),
    "anti_join_where_reads_the_left_table": Unseen(
        "SELECT t.k FROM t ANTI JOIN u ON t.k = u.k WHERE a > 2",
        "a name that is not a granted column",
        "SELECT k FROM t WHERE NOT EXISTS (SELECT 1 FROM u WHERE t.k = u.k AND u.a > 2)",
        "SELECT k FROM t WHERE NOT EXISTS (SELECT 1 FROM u WHERE t.k = u.k) AND t.a > 2",
    ),
}


@pytest.mark.parametrize("case", sorted(UNSEEN))
def test_must_fail_a_name_describe_cannot_see_refuses(con: Any, case: str) -> None:
    spec = UNSEEN[case]
    rows = _rows(con, spec.sql)
    assert isinstance(rows, list), rows
    assert _bag(rows) == _bag(_rows(con, spec.bound)), "DuckDB binds the unlisted column"
    assert _bag(rows) != _bag(_rows(con, spec.claimed)), "the old statement's reading differs"
    with pytest.raises(describe.DescribeError, match=spec.reason):
        describe.statement(spec.sql, keep_literal=_never, granted=SUBSET)


# An output alias that is not a plain passthrough, named like any other name the
# statement reads, refuses even when every column is listed: in a column-first
# clause (WHERE, GROUP BY, ON, the SELECT list, an aggregate) DuckDB binds an
# unlisted column of that name over the alias, and describe cannot rule one out.
# Each rendered on a8a0d2d (true with every column listed, but not provably so).
OUTPUT_NAME_REFUSES: dict[str, str] = {
    "group_by_output_name": (
        "SELECT CASE WHEN b > 50 THEN 'hi' ELSE 'lo' END AS band, COUNT(*) FROM t GROUP BY band"
    ),
    "where_output_name": (
        "SELECT CASE WHEN b > 50 THEN 'hi' ELSE 'lo' END AS band FROM t WHERE band = 'hi'"
    ),
    "having_alias_of_its_own_column": "SELECT k, SUM(b) AS b FROM t GROUP BY k HAVING b > 60",
    "order_alias_of_its_own_column": "SELECT k, SUM(b) AS b FROM t GROUP BY k ORDER BY b",
    "group_by_column_an_output_is_named": ("SELECT SUM(b) AS k, COUNT(*) AS n FROM t GROUP BY k"),
    "where_column_an_output_is_named": "SELECT a AS b FROM t WHERE b > 55",
    "on_column_an_output_is_named": (
        "SELECT t.k, COUNT(*) AS a FROM t JOIN u ON t.k = u.k AND u.a > 0 GROUP BY t.k"
    ),
    "output_named_like_its_table": "SELECT COUNT(*) AS t FROM t",
    "output_named_like_a_qualifier": "SELECT x.k, COUNT(*) AS x FROM t AS x GROUP BY x.k",
}


@pytest.mark.parametrize("case", sorted(OUTPUT_NAME_REFUSES))
def test_must_fail_an_output_named_like_another_name_refuses(con: Any, case: str) -> None:
    sql = OUTPUT_NAME_REFUSES[case]
    assert isinstance(_rows(con, sql), list), "a query DuckDB runs"
    with pytest.raises(describe.DescribeError, match="an output named like"):
        _say(sql)


# Still described: a passthrough alias (``k AS k``) is the column either way, and
# an alias no other name could be keeps DuckDB's alias-first ORDER BY and HAVING.
def test_a_passthrough_or_unshared_alias_is_still_described(con: Any) -> None:
    assert _say("SELECT k AS k FROM t WHERE k = 'x' ORDER BY k") == (
        f"Returns k from t where k is {MARK}, sorted by k"
    )
    _check_claims(
        con, "SELECT k AS k, COUNT(*) AS n FROM t GROUP BY k ORDER BY k NULLS FIRST", True
    )
    _check_claims(con, "SELECT k, COUNT(*) AS n FROM t GROUP BY k HAVING n > 1", False)


# The non-blocking round-4 refusals, each of a statement a8a0d2d rendered.
OTHER_REFUSES: dict[str, tuple[str, str]] = {
    # "keeping rows of t with no match ... where u.a ...": the WHERE drops them.
    "left_join_where_reads_the_joined_table": (
        "SELECT t.k FROM t LEFT JOIN u ON t.k = u.k WHERE u.a > 0",
        "a WHERE that reads the table of a LEFT JOIN",
    ),
    "left_join_where_bare_name_of_the_joined_table": (
        "SELECT t.k FROM t LEFT JOIN s ON t.k = s.k WHERE e IS NULL",
        "a WHERE that reads the table of a LEFT JOIN",
    ),
    # "where a": DuckDB keeps a <> 0, not a TRUE flag.
    "integer_column_as_condition": ("SELECT k FROM t WHERE a", "not typed BOOLEAN"),
    "integer_column_in_and": ("SELECT k FROM t WHERE flag AND (a)", "not typed BOOLEAN"),
    "integer_column_in_on": ("SELECT t.k FROM t JOIN s ON t.k = s.k AND s.e", "not typed BOOLEAN"),
    # "a value" would not say what two casts do to it.
    "cast_of_a_cast": (
        "SELECT k FROM t WHERE b = CAST(CAST(50.7 AS INTEGER) AS INTEGER)",
        "a CAST of a CAST",
    ),
    # Bare name two tables of the clause list: DuckDB refuses it as ambiguous.
    "ambiguous_bare_name": ("SELECT t.k FROM t JOIN u ON t.k = u.k WHERE a > 1", "more than one"),
    # A SEMI join's table outside its ON: DuckDB says "table not found".
    "semi_join_table_in_select": (
        "SELECT u.c FROM t SEMI JOIN u ON t.k = u.k",
        "a qualifier that is not a table this clause reads",
    ),
}


@pytest.mark.parametrize("case", sorted(OTHER_REFUSES))
def test_must_fail_round_four_refusals(case: str) -> None:
    sql, reason = OTHER_REFUSES[case]
    with pytest.raises(describe.DescribeError, match=reason):
        _say(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT t.k FROM t JOIN u ON t.k = s.k JOIN s ON u.k = s.k",
        "SELECT t.k FROM t SEMI JOIN u ON t.k = u.k JOIN s ON u.k = s.k",
    ],
    ids=["later_table", "semi_table_in_a_later_on"],
)
def test_must_fail_on_scope_reads_only_earlier_tables(con: Any, sql: str) -> None:
    """A join's ON reads the visible tables before it and its own table: DuckDB
    refuses a later table, or a SEMI / ANTI join's table, in it."""
    assert isinstance(_rows(con, sql), str), "DuckDB refuses it"
    with pytest.raises(describe.DescribeError, match="a qualifier that is not a table"):
        _say(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT k, COUNT(*) AS n FROM t GROUP BY k HAVING n",
        "SELECT k, MIN(flag) AS ok FROM t GROUP BY k HAVING (ok)",
    ],
    ids=["count", "boolean_aggregate"],
)
def test_must_fail_an_output_read_as_a_condition_refuses(con: Any, sql: str) -> None:
    """``HAVING n`` runs (DuckDB casts the count to a truth value), but "having
    number of rows" says nothing true: an output alias is never a condition."""
    assert isinstance(_rows(con, sql), list), "a query DuckDB runs"
    with pytest.raises(describe.DescribeError, match="an output read as a condition"):
        _say(sql)


def test_a_boolean_column_untyped_by_the_payload_refuses() -> None:
    """A bare column is a condition only when the payload says BOOLEAN: a grant
    that lists names without types reads no bare column as a condition."""
    untyped = {table: list(columns) for table, columns in GRANTED.items()}
    with pytest.raises(describe.DescribeError, match="not typed BOOLEAN"):
        describe.statement("SELECT k FROM t WHERE flag", keep_literal=_never, granted=untyped)
    assert _say("SELECT k FROM t WHERE flag") == "Returns k from t where flag"


def test_must_fail_a_bare_name_another_table_lists_refuses(con: Any) -> None:
    """In a statement over two tables, a bare name both list refuses even where
    DuckDB binds it (u is not visible after its SEMI JOIN, so ``a`` is t.a):
    printed bare, "where a" after "some match in u" reads as u's a."""
    sql = "SELECT k FROM t SEMI JOIN u ON t.k = u.k WHERE a > 2"
    assert _bag(_rows(con, sql)) == _bag(
        _rows(con, "SELECT k FROM t SEMI JOIN u ON t.k = u.k WHERE t.a > 2")
    )
    with pytest.raises(describe.DescribeError, match="another table of the statement lists"):
        _say(sql)
    # Qualified, it is stated with its table.
    qualified = "SELECT t.k FROM t SEMI JOIN u ON t.k = u.k WHERE t.a > 2"
    assert _say(qualified) == (
        f"Returns t.k from t, keeping only rows with some match in u on t.k is u.k "
        f"where t.a is more than {MARK}"
    )
    _check_claims(con, qualified, False)


# -- 3. the property: random core queries render true statements or refuse --------------

_COLUMNS: dict[str, dict[str, str]] = {
    "t": {"k": "str", "a": "int", "b": "int", "c": "str", "d": "date", "flag": "bool"},
    "u": {"k": "str", "a": "int", "c": "str", "flag": "bool"},
    "s": {"k": "str", "e": "int"},
}
_STRINGS = ["'x'", "'y'", "'SKU-1'", "'OTHER'", "'w'", "'q'"]
_PATTERNS = ["'SKU%'", "'sku%'", "'%1'", "'O%'", "'x'"]
_DATES = ["DATE '2024-02-01'", "CAST('2024-01-10' AS DATE)", "DATE '2024-03-01'"]


@dataclass
class Query:
    """A query of the core grammar, as parts the generator can also break."""

    outputs: list[str]
    source: str
    joins: list[str] = field(default_factory=list)
    distinct: bool = False
    where: str | None = None
    group: list[str] = field(default_factory=list)
    having: str | None = None
    order: list[str] = field(default_factory=list)
    limit: int | None = None
    ordered: bool = False  # ORDER BY fixes the order of every output row

    def sql(self) -> str:
        text = f"SELECT {'DISTINCT ' if self.distinct else ''}{', '.join(self.outputs)} "
        text += f"FROM {self.source}" + "".join(f" {j}" for j in self.joins)
        if self.where:
            text += f" WHERE {self.where}"
        if self.group:
            text += " GROUP BY " + ", ".join(self.group)
        if self.having:
            text += f" HAVING {self.having}"
        if self.order:
            text += " ORDER BY " + ", ".join(self.order)
        if self.limit is not None:
            text += f" LIMIT {self.limit}"
        return text


class _Gen:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)

    def pick(self, items: list[Any]) -> Any:
        return self.rng.choice(items)

    def chance(self, p: float) -> bool:
        return self.rng.random() < p

    def value(self, kind: str) -> str:
        if kind == "int":
            return self.pick(["1", "3", "5", "50", "-1", "CAST('6' AS INTEGER)"])
        if kind == "str":
            return self.pick(_STRINGS)
        if kind == "date":
            return self.pick(_DATES)
        return self.pick(["TRUE", "FALSE"])

    def atom(self, columns: list[tuple[str, str]]) -> str:
        ref, kind = self.pick(columns)
        roll = self.rng.random()
        if kind == "bool":
            return self.pick([ref, f"{ref} = {self.value('bool')}", f"{ref} IS NULL", f"NOT {ref}"])
        if roll < 0.1:
            return f"{ref} IS {'NOT ' if self.chance(0.5) else ''}NULL"
        if roll < 0.2:
            values = ", ".join(self.value(kind) for _ in range(self.rng.randint(1, 3)))
            return f"{ref} {'NOT ' if self.chance(0.4) else ''}IN ({values})"
        if roll < 0.3 and kind != "str":
            low, high = sorted([self.value(kind), self.value(kind)])
            return f"{ref} {'NOT ' if self.chance(0.4) else ''}BETWEEN {low} AND {high}"
        if roll < 0.45 and kind == "str":
            negate = "NOT " if self.chance(0.4) else ""
            return f"{ref} {negate}{self.pick(['LIKE', 'ILIKE'])} {self.pick(_PATTERNS)}"
        same = [c for c in columns if c[1] == kind and c[0] != ref]
        op = self.pick(["=", "<>", "<", "<=", ">", ">="])
        if same and roll < 0.55:
            return f"{ref} {op} {self.pick(same)[0]}"
        if roll < 0.6:
            return f"{self.value(kind)} {op} {ref}"
        return f"{ref} {op} {self.value(kind)}"

    def condition(self, columns: list[tuple[str, str]], depth: int = 0) -> str:
        roll = self.rng.random()
        if depth < 2 and roll < 0.35:
            word = self.pick(["AND", "OR"])
            left = self.condition(columns, depth + 1)
            right = self.condition(columns, depth + 1)
            text = f"{left} {word} {right}"
            return f"({text})" if self.chance(0.5) else text
        if depth < 2 and roll < 0.42:
            return f"NOT ({self.condition(columns, depth + 1)})"
        return self.atom(columns)

    def case(self, columns: list[tuple[str, str]]) -> str:
        branches = " ".join(
            f"WHEN {self.atom(columns)} THEN {self.pick(_STRINGS)}"
            for _ in range(self.rng.randint(1, 2))
        )
        default = f" ELSE {self.pick(_STRINGS)}" if self.chance(0.7) else ""
        return f"CASE {branches}{default} END"

    def query(self) -> Query:
        tables = ["t", "u", "s"]
        base = self.pick(["t", "t", "u"])
        names: dict[str, str] = {}
        aliases = iter(self.rng.sample(["x", "y", "z"], 3))
        alias = next(aliases) if self.chance(0.3) else None
        names[base] = alias or base
        source = f"{base} AS {alias}" if alias else base
        joins: list[str] = []
        visible = [base]
        others = [t for t in tables if t != base]
        count = self.pick([0, 0, 0, 1, 1, 2])
        for i, table in enumerate(self.rng.sample(others, count)):
            alias = next(aliases) if self.chance(0.3) else None
            names[table] = alias or table
            kinds = ["JOIN", "SEMI JOIN", "ANTI JOIN"] + (["LEFT JOIN"] if count == 1 else [])
            kind = self.pick(kinds)
            left = self.pick(visible)
            on = f"{names[left]}.k = {names[table]}.k"
            if self.chance(0.3):
                extra = [
                    (f"{names[table]}.{c}", k) for c, k in _COLUMNS[table].items() if k == "int"
                ]
                if extra:
                    ref, _kind = self.pick(extra)
                    on += f" AND {ref} {self.pick(['>', '<=', '<>'])} {self.value('int')}"
            joins.append(
                f"{kind} {names[table] if names[table] == table else f'{table} AS {names[table]}'} ON {on}"
            )
            if kind in {"JOIN", "LEFT JOIN"}:
                visible.append(table)
            del i
        qualify = len(names) > 1 or self.chance(0.5)
        columns = [
            (f"{names[t]}.{c}" if qualify else c, kind)
            for t in visible
            for c, kind in _COLUMNS[t].items()
        ]
        query = Query(outputs=[], source=source, joins=joins)
        if self.chance(0.45):
            self.aggregate_query(query, columns)
        else:
            self.plain_query(query, columns)
        if self.chance(0.5):
            query.where = self.condition(columns)
        return query

    def plain_query(self, query: Query, columns: list[tuple[str, str]]) -> None:
        star = self.chance(0.1)
        if star:
            query.outputs = ["*"]
            return
        chosen = self.rng.sample(columns, self.rng.randint(1, 3))
        outputs = [ref for ref, _kind in chosen]
        if self.chance(0.25):
            outputs.append(f"{self.case(columns)} AS band")
        query.outputs = outputs
        query.distinct = self.chance(0.2)
        self.sort(query)

    def aggregate_query(self, query: Query, columns: list[tuple[str, str]]) -> None:
        keys = self.rng.sample(columns, self.rng.randint(0, 2))
        outputs = [ref for ref, _kind in keys]
        group: list[str] = []
        for position, (ref, _kind) in enumerate(keys, 1):
            group.append(self.pick([ref, str(position), f"({position})"]))
        if self.chance(0.2):
            case = self.case(columns)
            outputs.append(f"{case} AS band")
            group.append(self.pick([case, "band", str(len(outputs))]))
        numeric = [c for c in columns if c[1] == "int"]
        aggregates = []
        for _ in range(self.rng.randint(1, 2)):
            ref, kind = self.pick(columns)
            options = ["COUNT(*)", "COUNT(1)", f"COUNT({ref})", f"COUNT(DISTINCT {ref})"]
            if kind != "bool":
                options += [f"MIN({ref})", f"MAX({ref})"]
            if numeric:
                number = self.pick(numeric)[0]
                options += [f"SUM({number})", f"AVG({number})"]
            aggregates.append(self.pick(options))
        # An output may be named after a granted column (``SUM(b) AS b``): DuckDB
        # then binds a bare ``b`` in HAVING and ORDER BY to the output, and in
        # WHERE and GROUP BY to the column. The statement must say which.
        names = [
            self.pick([f"m{i}", self.pick(["k", "a", "b", "c", "e"])]) if self.chance(0.5) else None
            for i in range(len(aggregates))
        ]
        named = [f"{agg} AS {n}" if n else agg for agg, n in zip(aggregates, names, strict=True)]
        query.outputs = outputs + named
        query.group = group
        if self.chance(0.4):
            index = self.rng.randrange(len(aggregates))
            agg = aggregates[index]
            op = self.pick([">", ">=", "<", "<=", "<>", "="])
            threshold = (
                self.pick(["1", "2", "50"]) if agg.startswith(("COUNT", "SUM", "AVG")) else None
            )
            if threshold is not None:
                spelled = names[index] if names[index] and self.chance(0.5) else agg
                query.having = f"{spelled} {op} {threshold}"
        self.sort(query)

    def sort(self, query: Query) -> None:
        if not self.chance(0.6):
            return
        width = len(query.outputs)
        positions = list(range(1, width + 1))
        self.rng.shuffle(positions)
        full = self.chance(0.6)
        keys = positions if full else positions[: self.rng.randint(1, width)]
        order = []
        for position in keys:
            output = query.outputs[position - 1]
            name = output.rsplit(" AS ", 1)[1] if " AS " in output else None
            spelled = self.pick([str(position), f"({position})"] + ([name] if name else []))
            if not name and " " not in output and "(" not in output and self.chance(0.4):
                spelled = output
            direction = self.pick(["", " DESC"])
            nulls = self.pick(["", "", " NULLS FIRST", " NULLS LAST"])
            order.append(f"{spelled}{direction}{nulls}")
        query.order = order
        query.ordered = full
        if full and self.chance(0.5):
            query.limit = self.rng.randint(1, 5)


def _flip(sql: str, rng: random.Random) -> str | None:
    """One attribute of ``sql`` flipped to its nearest inverse, or None if none applies."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    swaps: list[Callable[[], None]] = []
    pairs = {
        exp.EQ: exp.NEQ,
        exp.NEQ: exp.EQ,
        exp.GT: exp.LTE,
        exp.LTE: exp.GT,
        exp.LT: exp.GTE,
        exp.GTE: exp.LT,
    }
    for node in tree.walk():
        if type(node) in pairs:
            swaps.append(
                lambda n=node: n.replace(pairs[type(n)](this=n.this, expression=n.expression))
            )
        if isinstance(node, (exp.Like, exp.ILike)):
            swaps.append(lambda n=node: n.set("negate", not n.args.get("negate")))
        if isinstance(node, (exp.And, exp.Or)):
            other = exp.Or if isinstance(node, exp.And) else exp.And
            swaps.append(lambda n=node, o=other: n.replace(o(this=n.this, expression=n.expression)))
        if isinstance(node, exp.Ordered):
            swaps.append(lambda n=node: n.set("desc", not n.args.get("desc")))
            swaps.append(lambda n=node: n.set("nulls_first", not n.args.get("nulls_first")))
        if isinstance(node, exp.Join):
            kind = str(node.args.get("kind") or "").upper()
            if kind in {"SEMI", "ANTI"}:
                swaps.append(
                    lambda n=node, k=kind: n.set("kind", "ANTI" if k == "SEMI" else "SEMI")
                )
        if type(node) in (exp.Sum, exp.Avg, exp.Min, exp.Max):
            other = {exp.Sum: exp.Avg, exp.Avg: exp.Sum, exp.Min: exp.Max, exp.Max: exp.Min}[
                type(node)
            ]
            swaps.append(lambda n=node, o=other: n.replace(o(this=n.this)))
        if isinstance(node, exp.Is):
            swaps.append(lambda n=node: n.replace(exp.Not(this=n.copy())))
        if isinstance(node, exp.Limit):
            swaps.append(
                lambda n=node: n.set("expression", exp.Literal.number(int(n.expression.name) + 1))
            )
    if isinstance(tree, exp.Select):
        swaps.append(
            lambda: tree.set("distinct", None if tree.args.get("distinct") else exp.Distinct())
        )
    if not swaps:
        return None
    rng.choice(swaps)()
    return tree.sql(dialect="duckdb")


PROPERTY_QUERIES = 1500


def test_property_core_queries_render_true_statements_or_refuse(con: Any) -> None:
    rendered = refused = errored = flipped = 0
    reasons: dict[str, int] = {}
    for seed in range(PROPERTY_QUERIES):
        query = _Gen(seed).query()
        sql = query.sql()
        if isinstance(_rows(con, sql), str):
            errored += 1  # not a query DuckDB runs: no reading would carry it
            continue
        try:
            statement = _check_claims(con, sql, query.ordered)
        except describe.DescribeError as exc:
            refused += 1
            reasons[str(exc)] = reasons.get(str(exc), 0) + 1
            continue
        rendered += 1
        rng = random.Random(seed)
        mutant = _flip(sql, rng)
        if mutant is None or mutant == sqlglot.parse_one(sql, read="duckdb").sql(dialect="duckdb"):
            continue
        try:
            after = _say(mutant)
        except describe.DescribeError:
            continue
        flipped += 1
        assert after != statement, (sql, mutant, statement)
    # Not vacuous: most generated core queries run on DuckDB and render.
    assert rendered >= 0.75 * PROPERTY_QUERIES, (rendered, refused, errored, reasons)
    assert flipped >= 0.5 * rendered, (flipped, rendered)


# -- 4. the property: random queries outside the core grammar always refuse -------------

# Each takes a core query and breaks it in one place, the way a model might.
Breaker = Callable[[Query, random.Random], Query | str]


def _and_where(query: Query, atom: str) -> Query:
    where = f"({query.where}) {'AND'} {atom}" if query.where else atom
    return replace(query, where=where)


def _base_name(query: Query) -> str:
    return query.source.split(" AS ")[-1]


def _base_table(query: Query) -> str:
    return query.source.split(" AS ")[0]


BREAKERS: dict[str, Breaker] = {
    "neq_null": lambda q, r: _and_where(q, f"{_base_name(q)}.k <> NULL"),
    "eq_null": lambda q, r: _and_where(q, f"{_base_name(q)}.k = NULL"),
    "null_in_list": lambda q, r: _and_where(q, f"{_base_name(q)}.k IN ('x', NULL)"),
    "null_bound": lambda q, r: _and_where(q, f"{_base_name(q)}.k BETWEEN NULL AND 'z'"),
    "like_null": lambda q, r: _and_where(q, f"{_base_name(q)}.k LIKE NULL"),
    "cast_null": lambda q, r: _and_where(q, f"{_base_name(q)}.k = CAST(NULL AS VARCHAR)"),
    "or_neq_null": lambda q, r: replace(
        q, where=f"{q.where or 'TRUE'} OR {_base_name(q)}.k <> NULL"
    ),
    "is_distinct_from": lambda q, r: _and_where(q, f"{_base_name(q)}.k IS DISTINCT FROM 'x'"),
    "is_true": lambda q, r: _and_where(q, f"{_base_name(q)}.k IS TRUE"),
    "two_values": lambda q, r: _and_where(q, "1 = 1"),
    "bare_true": lambda q, r: _and_where(q, "TRUE"),
    "like_escape": lambda q, r: _and_where(q, f"{_base_name(q)}.k LIKE 'x!%' ESCAPE '!'"),
    "similar_to": lambda q, r: _and_where(q, f"{_base_name(q)}.k SIMILAR TO 'x.*'"),
    "function": lambda q, r: _and_where(q, f"LOWER({_base_name(q)}.k) = 'x'"),
    "coalesce": lambda q, r: _and_where(q, f"COALESCE({_base_name(q)}.k, 'x') = 'x'"),
    "cast_column": lambda q, r: _and_where(q, f"CAST({_base_name(q)}.k AS VARCHAR) = 'x'"),
    "try_cast": lambda q, r: _and_where(q, f"{_base_name(q)}.k = TRY_CAST('x' AS VARCHAR)"),
    "arithmetic": lambda q, r: _and_where(q, f"{_base_name(q)}.k || 'x' = 'xx'"),
    "interval": lambda q, r: _and_where(
        q, "CAST('2024-01-01' AS DATE) < CURRENT_DATE - INTERVAL 3 DAY"
    ),
    "exists": lambda q, r: _and_where(q, "EXISTS (SELECT 1 FROM s AS q WHERE q.e > 1)"),
    "exists_reused_alias": lambda q, r: _and_where(
        q, f"EXISTS (SELECT 1 FROM s AS {_base_name(q)} WHERE {_base_name(q)}.e > 1)"
    ),
    "in_subquery": lambda q, r: _and_where(q, f"{_base_name(q)}.k IN (SELECT k FROM s)"),
    "scalar_subquery": lambda q, r: replace(q, outputs=[*q.outputs, "(SELECT MAX(e) FROM s)"]),
    "aggregate_in_where": lambda q, r: _and_where(q, "COUNT(*) > 1"),
    "wrapped_in_subquery": lambda q, r: f"SELECT * FROM ({q.sql()}) AS q",
    "wrapped_in_cte": lambda q, r: f"WITH q AS ({q.sql()}) SELECT * FROM q",
    "union": lambda q, r: f"({q.sql()}) UNION ALL ({q.sql()})",
    "except": lambda q, r: f"({q.sql()}) EXCEPT ({q.sql()})",
    "window": lambda q, r: replace(q, outputs=[*q.outputs, "ROW_NUMBER() OVER () AS rn"]),
    "qualify": lambda q, r: (
        f"{q.sql().split(' ORDER BY ')[0].split(' LIMIT ')[0]} QUALIFY ROW_NUMBER() OVER () = 1"
    ),
    "offset": lambda q, r: f"{q.sql()} OFFSET 1",
    "limit_percent": lambda q, r: f"{q.sql().split(' LIMIT ')[0]} LIMIT 50%",
    "self_join": lambda q, r: replace(
        q, joins=[*q.joins, f"JOIN {_base_table(q)} AS q ON q.k = {_base_name(q)}.k"]
    ),
    "alias_reuse": lambda q, r: replace(
        q,
        joins=[
            *q.joins,
            f"JOIN {'s' if 's' not in q.sql() else 'u'} AS {_base_table(q)} "
            f"ON {_base_table(q)}.k = 'x'",
        ],
    ),
    "schema_qualified": lambda q, r: replace(q, source=f"main.{q.source}"),
    "catalog_qualified": lambda q, r: replace(q, source=f"memory.main.{q.source}"),
    "tablesample": lambda q, r: replace(q, source=f"{q.source} TABLESAMPLE 50%"),
    "table_function": lambda q, r: replace(q, joins=[*q.joins, "JOIN range(3) AS q ON TRUE"]),
    "values": lambda q, r: replace(q, joins=[*q.joins, "JOIN (VALUES ('x')) AS q(v) ON q.v = 'x'"]),
    "cross_join": lambda q, r: replace(q, joins=[*q.joins, "CROSS JOIN s AS q"]),
    "comma_join": lambda q, r: replace(q, joins=[*q.joins, ", s AS q"]),
    "using": lambda q, r: replace(q, joins=[*q.joins, "JOIN s AS q USING (k)"]),
    "right_join": lambda q, r: replace(
        q, joins=[*q.joins, f"RIGHT JOIN s AS q ON q.k = {_base_name(q)}.k"]
    ),
    "full_join": lambda q, r: replace(
        q, joins=[*q.joins, f"FULL JOIN s AS q ON q.k = {_base_name(q)}.k"]
    ),
    "positional_join": lambda q, r: replace(q, joins=[*q.joins, "POSITIONAL JOIN s AS q"]),
    "natural_join": lambda q, r: replace(q, joins=[*q.joins, "NATURAL JOIN s AS q"]),
    "asof_join": lambda q, r: replace(
        q, joins=[*q.joins, f"ASOF JOIN s AS q ON q.k >= {_base_name(q)}.k"]
    ),
    "second_left_join": lambda q, r: replace(
        q,
        joins=[
            f"LEFT JOIN s AS q ON q.k = {_base_name(q)}.k",
            f"LEFT JOIN s AS q2 ON q2.k = {_base_name(q)}.k",
        ],
    ),
    "join_without_on": lambda q, r: replace(q, joins=[*q.joins, "JOIN s AS q ON TRUE"]),
    "star_exclude": lambda q, r: replace(q, outputs=["* EXCLUDE (k)"], order=[], limit=None),
    "star_replace": lambda q, r: replace(q, outputs=["* REPLACE (k AS k)"], order=[], limit=None),
    "star_rename": lambda q, r: replace(q, outputs=["* RENAME (k AS kk)"], order=[], limit=None),
    "columns_expression": lambda q, r: replace(q, outputs=[*q.outputs, "COLUMNS('k')"]),
    "literal_output": lambda q, r: replace(q, outputs=[*q.outputs, "'zq_label'"]),
    "arithmetic_output": lambda q, r: replace(q, outputs=[*q.outputs, "1 + 1"]),
    "function_output": lambda q, r: replace(q, outputs=[*q.outputs, f"UPPER({_base_name(q)}.k)"]),
    "simple_case": lambda q, r: replace(
        q, outputs=[*q.outputs, f"CASE {_base_name(q)}.k WHEN 'x' THEN 'p' END"]
    ),
    "case_null_label": lambda q, r: replace(
        q, outputs=[*q.outputs, f"CASE WHEN {_base_name(q)}.k = 'x' THEN NULL ELSE 'p' END"]
    ),
    "case_column_label": lambda q, r: replace(
        q, outputs=[*q.outputs, f"CASE WHEN {_base_name(q)}.k = 'x' THEN {_base_name(q)}.k END"]
    ),
    "sum_distinct": lambda q, r: replace(q, outputs=[*q.outputs, "SUM(DISTINCT 1)"]),
    "aggregate_of_expression": lambda q, r: replace(q, outputs=[*q.outputs, "SUM(1 + 1)"]),
    "filter": lambda q, r: replace(q, outputs=[*q.outputs, "COUNT(*) FILTER (WHERE TRUE)"]),
    "max_n": lambda q, r: replace(q, outputs=[*q.outputs, f"MAX({_base_name(q)}.k, 2)"]),
    "distinct_on": lambda q, r: (
        f"SELECT DISTINCT ON ({_base_name(q)}.k) "
        f"{q.sql().split('SELECT ', 1)[1].removeprefix('DISTINCT ')}"
    ),
    "group_by_all": lambda q, r: replace(q, group=["ALL"]),
    "rollup": lambda q, r: replace(q, group=[f"ROLLUP ({_base_name(q)}.k)"]),
    "group_by_expression": lambda q, r: replace(q, group=[*q.group, f"{_base_name(q)}.k || 'x'"]),
    "group_by_constant": lambda q, r: replace(q, group=[*q.group, "(1 + 0)"]),
    "order_by_expression": lambda q, r: replace(q, order=[*q.order, f"{_base_name(q)}.k || 'x'"]),
    "order_by_collate": lambda q, r: replace(
        q, order=[*q.order, f"{_base_name(q)}.k COLLATE NOCASE"]
    ),
    "order_by_ordinal_over_star": lambda q, r: replace(
        q, outputs=["*", f"{_base_name(q)}.k"], group=[], having=None, order=["2"], limit=None
    ),
    "ordinal_out_of_range": lambda q, r: replace(q, order=[*q.order, "99"]),
    "lateral": lambda q, r: replace(
        q, joins=[*q.joins, f"JOIN LATERAL (SELECT {_base_name(q)}.k AS v) AS q ON TRUE"]
    ),
    "unpivot_source": lambda q, r: (
        f"SELECT * FROM (UNPIVOT {_base_table(q)} ON k INTO NAME n VALUE v)"
    ),
    "pivot_source": lambda q, r: f"SELECT * FROM (PIVOT {_base_table(q)} ON k USING COUNT(*))",
    "named_parameter": lambda q, r: _and_where(q, f"{_base_name(q)}.k = $1"),
    "json_path": lambda q, r: replace(q, outputs=[*q.outputs, f"{_base_name(q)}.k ->> '$.a'"]),
}


def test_property_queries_outside_the_core_grammar_always_refuse() -> None:
    checked = 0
    for seed in range(len(BREAKERS) * 8):
        rng = random.Random(10_000 + seed)
        name = sorted(BREAKERS)[seed % len(BREAKERS)]
        query = _Gen(10_000 + seed).query()
        broken = BREAKERS[name](query, rng)
        sql = broken if isinstance(broken, str) else broken.sql()
        try:
            sqlglot.parse_one(sql, read="duckdb")
        except Exception:  # noqa: BLE001 - a break sqlglot cannot read proves nothing
            continue
        with pytest.raises(describe.DescribeError) as refused:
            _say(sql)
        assert "unparsable" not in str(refused.value), (name, sql)
        checked += 1
    assert checked >= 0.9 * len(BREAKERS) * 8, checked


def test_every_breaker_parses_and_refuses() -> None:
    """Not vacuous: each break parses on at least one base query (so the refusal
    above is about the construct, not about sqlglot failing to read it)."""
    parsed: set[str] = set()
    for name, breaker in BREAKERS.items():
        for seed in range(20):
            query = _Gen(20_000 + seed).query()
            broken = breaker(query, random.Random(seed))
            sql = broken if isinstance(broken, str) else broken.sql()
            try:
                sqlglot.parse_one(sql, read="duckdb")
            except Exception:  # noqa: BLE001
                continue
            with pytest.raises(describe.DescribeError):
                _say(sql)
            parsed.add(name)
            break
    assert parsed == set(BREAKERS), sorted(set(BREAKERS) - parsed)


# -- 5. names that read as the statement's own words are quoted ---------------------------


def test_a_name_that_is_a_phrase_word_is_quoted(con: Any) -> None:
    granted = {"rows": ["first", "is", "Total Kg"]}
    con.execute('CREATE TEMP TABLE "rows" ("first" INTEGER, "is" INTEGER, "Total Kg" INTEGER)')
    con.execute('INSERT INTO "rows" VALUES (1, 2, 3), (4, NULL, 6)')
    sql = 'SELECT "first", "Total Kg" FROM "rows" WHERE "is" IS NOT NULL ORDER BY 1 LIMIT 1'
    text = describe.statement(sql, keep_literal=_always, granted=granted)
    assert text == (
        'Returns "first" and "Total Kg" from "rows" where "is" is not empty, '
        'sorted by "first", first 1 row'
    )
    assert _rows(con, compile_statement(text)) == _rows(con, sql)


# -- 6. each layer refuses on its own ------------------------------------------------------

# The grammar is the guard; the renderer and the leaf check refuse what they do
# not know as a second net, so removing a grammar rule alone can leave a
# statement-level test green. These run the grammar alone, so each rule is
# shown to refuse by itself.
GRAMMAR_REFUSES: dict[str, str] = {
    "null_operand": "SELECT k FROM t WHERE c <> NULL",
    "null_in_list": "SELECT k FROM t WHERE a IN (1, NULL)",
    "cast_null": "SELECT k FROM t WHERE a = CAST(NULL AS INTEGER)",
    "two_values": "SELECT k FROM t WHERE 1 = 1",
    "column_in_list": "SELECT k FROM t WHERE a IN (b, 2)",
    "column_bound": "SELECT k FROM t WHERE a BETWEEN b AND 3",
    "column_label": "SELECT CASE WHEN a > 1 THEN c END FROM t",
    "aggregate_in_where": "SELECT k FROM t WHERE COUNT(*) > 1",
    "aggregate_in_case": "SELECT CASE WHEN COUNT(*) > 1 THEN 'p' END FROM t",
    "group_by_case_not_output": (
        "SELECT COUNT(*) FROM t GROUP BY CASE WHEN a > 1 THEN 'p' ELSE 'q' END"
    ),
    "limit_decimal": "SELECT k FROM t LIMIT 2.5",
    "no_table": "SELECT 1",
    "function": "SELECT k FROM t WHERE LOWER(c) = 'x'",
    "subquery": "SELECT k FROM t WHERE a IN (SELECT a FROM u)",
}


def _grammar(sql: str) -> None:
    tree = sqlglot.parse_one(sql, read="duckdb")
    describe._strip_comments(tree)
    describe._audit(tree)
    describe._Grammar(tree).check()


@pytest.mark.parametrize("case", sorted(GRAMMAR_REFUSES))
def test_must_fail_the_grammar_refuses_on_its_own(case: str) -> None:
    with pytest.raises(describe.DescribeError):
        _grammar(GRAMMAR_REFUSES[case])
    with pytest.raises(describe.DescribeError):
        _say(GRAMMAR_REFUSES[case])


def test_the_grammar_accepts_every_core_construct() -> None:
    for spec in CONSTRUCTS.values():
        _grammar(spec.sql)


def test_must_fail_a_node_no_position_accepted_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The grammar's last check: every node of the tree was accepted somewhere.
    A position method that forgot to descend (here: CASE) still refuses."""

    def shallow(self: Any, node: exp.Expression) -> None:
        self.ok(node)

    monkeypatch.setattr(describe._Grammar, "case", shallow)
    with pytest.raises(describe.DescribeError, match="does not describe"):
        _say("SELECT CASE WHEN a > 1 THEN c END FROM t")


@pytest.mark.parametrize(
    "node",
    [exp.Null(), exp.Anonymous(this="f"), exp.Subquery(this=exp.select("1"))],
    ids=["null", "function", "subquery"],
)
def test_must_fail_the_renderer_refuses_what_it_does_not_name(node: exp.Expression) -> None:
    with pytest.raises(describe.DescribeError):
        describe._operand(node)
    with pytest.raises(describe.DescribeError):
        describe._condition(node)
    with pytest.raises(describe.DescribeError):
        describe._projection(node, 1)
