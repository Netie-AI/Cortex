"""PICK-READING-01: ``plan_sql.describe`` states a gated SQL with no value in it.

The statement is what a split reconfirm shows the user for each reading
(founder ruling 2026-10-29: a plain-language description with a row count and
no values). Every literal in the whole tree is redacted before rendering, every
comment is stripped (amendment r3: a comment is a value channel), and any leaf
the renderer does not know refuses. Each redaction case uses sentinel literals
that occur nowhere else in the SQL, so a sentinel in the output can only be a
leak.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from CortexOS.plan_sql import describe
from tests.test_plan_sql.test_c_loop_a_plan_sql import GOOD_SQL
from tests.test_plan_sql.test_plan_sql_vote import KG_SQL, SKU_SQL

CORPUS = Path(__file__).resolve().parents[1] / "test_execution" / "hostile_sql_corpus.json"


def _never(_text: str) -> bool:
    return False


def _say(sql: str) -> str:
    return describe.statement(sql, keep_literal=_never)


def test_fixture_readings_are_distinct_and_name_their_aggregate() -> None:
    good, sku, kg = (_say(sql) for sql in (GOOD_SQL, SKU_SQL, KG_SQL))
    assert len({good, sku, kg}) == 3
    assert "number of rows" in good and "txn_type" in good
    assert "sorted by n descending" in good
    assert "number of distinct sku" in sku
    assert "total of quantity_kg" in kg
    for text in (good, sku, kg):
        assert text.startswith("Returns ")
        assert "for each txn_type" in text and "from transactions" in text


@pytest.mark.parametrize(
    ("sql", "needle"),
    [
        ("WITH x AS (SELECT a FROM t) SELECT a FROM x", "computed with 1 nested query"),
        ("SELECT a FROM t WHERE b IN (SELECT b FROM u)", "computed with 1 nested query"),
        ("SELECT a, ROW_NUMBER() OVER (ORDER BY b) AS r FROM t", "window functions"),
        ("SELECT a FROM t UNION SELECT a FROM u", "combining 2 query results"),
    ],
    ids=["cte", "subquery", "window", "set_operation"],
)
def test_complex_queries_get_the_honest_fallback(sql: str, needle: str) -> None:
    text = _say(sql)
    assert text.startswith("Returns ")
    assert needle in text, text


# Each case: SQL whose sentinels (zq_*, 4711*, 2019*) occur nowhere else.
REDACT = {
    "projection_case": "SELECT CASE WHEN qty > 4711 THEN 'zq_high' ELSE 'zq_low' END AS band FROM t",
    "group_by_case": (
        "SELECT COUNT(*) AS n FROM t "
        "GROUP BY CASE WHEN qty > 4711 THEN 'zq_big' ELSE 'zq_small' END"
    ),
    "order_by_case": (
        "SELECT a FROM t ORDER BY CASE WHEN qty > 4711 THEN 'zq_big' ELSE 'zq_small' END"
    ),
    "order_by_coalesce": "SELECT a FROM t ORDER BY COALESCE(x, 'zq_na')",
    "where_coalesce": "SELECT a FROM t WHERE COALESCE(c, 'zq_c') = 'zq_d'",
    "where_cast": "SELECT a FROM t WHERE c = CAST('zq_e' AS VARCHAR)",
    "where_date": "SELECT a FROM t WHERE d = DATE '2019-07-14'",
    "where_timestamp": "SELECT a FROM t WHERE d = TIMESTAMP '2019-07-14 08:09:10'",
    "where_interval": "SELECT a FROM t WHERE d > CURRENT_DATE - INTERVAL 4711 DAY",
    "where_function_argument": "SELECT a FROM t WHERE STRFTIME(d, '%Y') = '2019'",
    "where_like": "SELECT a FROM t WHERE LOWER(c) LIKE '%zq_f%'",
    "where_in": "SELECT a FROM t WHERE c IN ('zq_g', 'zq_h')",
    "where_between": "SELECT a FROM t WHERE b BETWEEN 4711 AND 4712",
    "join_on": "SELECT t.a FROM t JOIN u ON t.id = u.id AND u.kind = 'zq_i'",
    "having": "SELECT a, SUM(x) AS s FROM t GROUP BY a HAVING SUM(x) > 4711.5",
    "negative": "SELECT a FROM t WHERE b < -4711",
    "boolean": "SELECT a FROM t WHERE flag = TRUE",
    "escape_string": "SELECT a FROM t WHERE c = E'zq_j'",
    "dollar_string": "SELECT a FROM t WHERE c = $$zq_k$$",
    "unaliased_literal": "SELECT 'zq_l' FROM t",
    "subquery": "SELECT a FROM t WHERE b IN (SELECT b FROM u WHERE c = 'zq_m')",
    "cte": "WITH x AS (SELECT a FROM t WHERE c = 'zq_n') SELECT a FROM x",
    "offset": "SELECT a FROM t LIMIT 5 OFFSET 4711",
}
_SENTINEL = re.compile(r"zq_\w+|4711(?:\.5)?|4712|2019[-\d: ]*|'%Y'|%Y|-\s*<a value>")


# The honest fallback names only output columns and tables, so a literal under
# it is not shown even as the mark.
_FALLBACK_CASES = {"cte", "subquery"}


@pytest.mark.parametrize("case", sorted(REDACT))
def test_every_literal_in_every_clause_is_redacted(case: str) -> None:
    sql = REDACT[case]
    text = _say(sql)
    if case in _FALLBACK_CASES:
        assert "nested query" in text, text
    else:
        assert describe.MARK in text, text
    sentinels = {m.group(0).strip() for m in _SENTINEL.finditer(sql)} - {"-"}
    assert sentinels or case == "boolean", sql
    for sentinel in sentinels:
        assert sentinel.lower() not in text.lower(), (sentinel, text)
    if case == "negative":
        assert "-" not in text, text
    if case == "boolean":
        assert "true" not in text.lower(), text


def test_the_limit_count_is_the_only_literal_shown() -> None:
    assert "first 7 rows" in _say("SELECT a FROM t LIMIT 7")
    assert "first 1 row" in _say("SELECT a FROM t LIMIT 1")


def test_a_kept_literal_is_the_only_one_shown() -> None:
    sql = "SELECT a FROM t WHERE c = 'zq_keep' AND d = 'zq_drop'"
    text = describe.statement(sql, keep_literal=lambda value: value == "zq_keep")
    assert "zq_keep" in text and "zq_drop" not in text
    assert describe.MARK in text


def test_ordinals_name_the_projection_not_a_value() -> None:
    text = _say("SELECT txn_type, COUNT(*) AS n FROM transactions GROUP BY 1 ORDER BY 2 DESC")
    assert "for each txn_type" in text
    assert "sorted by n descending" in text
    assert describe.MARK not in text
    unaliased = _say("SELECT txn_type, COUNT(*) FROM transactions GROUP BY 1 ORDER BY 2")
    assert "sorted by number of rows" in unaliased
    with pytest.raises(describe.DescribeError):
        _say("SELECT a FROM t GROUP BY 3")
    with pytest.raises(describe.DescribeError):
        _say("SELECT a FROM t ORDER BY 0")


@pytest.mark.parametrize(
    "sql",
    ["SELECT j ->> '$.name' FROM t", "SELECT a FROM t WHERE c = $1", "SELECT a FROM t WHERE c = ?"],
    ids=["json_path", "dollar_placeholder", "question_placeholder"],
)
def test_an_unknown_leaf_refuses(sql: str) -> None:
    with pytest.raises(describe.DescribeError):
        _say(sql)


def test_unparsable_sql_refuses() -> None:
    with pytest.raises(describe.DescribeError):
        _say("SELECT FROM WHERE (")


# -- amendment r3: comments ------------------------------------------------------------

# Comments on nodes the renderer prints as SQL text (a computed projection, a
# WHERE operand, a CASE, an ORDER BY expression) leak without the strip. The
# others sit where the renderer prints only identifiers; they are kept so a
# renderer change cannot open them, and the leaf check refuses any comment the
# strip missed.
COMMENTED = {
    "projection": "SELECT a, COALESCE(b /* zq_c1 */, c) AS k FROM t",
    "from_table": "SELECT a FROM t /* zq_c2 */",
    "where": "SELECT a FROM t WHERE COALESCE(b, c /* zq_c3 */) > 1 AND c IS NULL",
    "case": "SELECT CASE WHEN q /* zq_c4 */ > 1 THEN 'x' ELSE 'y' END AS k FROM t",
    "order_by_fallback": "SELECT a FROM t ORDER BY COALESCE(a, b) /* zq_c5 */",
    "inside_cte": "WITH x AS (SELECT a /* zq_c6 */ FROM t) SELECT a FROM x",
    "leading": "/* zq_c7 */ SELECT a FROM t",
    "trailing_line": "SELECT a FROM t -- zq_c8",
    "trailing_block": "SELECT a FROM t /* zq_c9 */",
}


@pytest.mark.parametrize("case", sorted(COMMENTED))
def test_a_comment_never_reaches_the_statement(case: str) -> None:
    sql = COMMENTED[case]
    tree = sqlglot.parse_one(sql, read="duckdb")
    # The channel is real: sqlglot keeps the comment and prints it.
    assert "zq_c" in tree.sql(dialect="duckdb"), case
    text = _say(sql)
    assert "zq_c" not in text, text
    assert "/*" not in text and "--" not in text, text


def test_a_comment_that_survives_stripping_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed if a later sqlglot keeps comments somewhere stripping does not reach."""
    monkeypatch.setattr(describe, "_strip_comments", lambda tree: None)
    with pytest.raises(describe.DescribeError, match="comment"):
        _say("SELECT a /* zq_c1 */ FROM t")


# -- the hostile corpus ---------------------------------------------------------------


def _corpus() -> list[str]:
    return [case["sql"] for case in json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]]


def test_hostile_corpus_never_prints_a_literal_or_a_comment() -> None:
    rendered = refused = 0
    for sql in _corpus():
        try:
            text = _say(sql)
        except describe.DescribeError:
            refused += 1
            continue
        rendered += 1
        assert "/*" not in text and "--" not in text, (sql, text)
        try:
            tree = sqlglot.parse_one(sql, read="duckdb")
        except Exception:  # noqa: BLE001 - describe rendered it, so it parsed
            raise AssertionError(sql) from None
        limit = tree.args.get("limit")
        shown = (
            limit.expression.name
            if isinstance(limit, exp.Limit) and limit.expression is not None
            else None
        )
        literals = {node.name for node in tree.walk() if isinstance(node, describe._VALUE_TYPES)}
        for literal in literals:
            if len(literal) < 3 or literal == shown:
                continue
            pattern = rf"(?<![\w.]){re.escape(literal)}(?![\w.])"
            assert not re.search(pattern, text, re.IGNORECASE), (literal, sql, text)
        comments = [c for node in tree.walk() for c in (node.comments or [])]
        for comment in comments:
            if comment.strip():
                assert comment.strip() not in text, (comment, sql, text)
    assert rendered >= 90 and refused >= 1, (rendered, refused)


# -- the cap and purity --------------------------------------------------------------


def test_cap_statement_bounds_the_text() -> None:
    assert describe.cap_statement("x" * 300) == "x" * 300
    capped = describe.cap_statement("x" * 1000)
    assert capped.endswith(" (shortened)")
    assert len(capped) == describe.STATEMENT_CAP + len(describe.SHORTENED) <= 312
    wide = ", ".join(f"col_{i}" for i in range(200))
    text = _say(f"SELECT {wide} FROM t")
    assert len(text) <= 312 and text.endswith("(shortened)")


def test_statement_is_pure_and_never_sees_the_question() -> None:
    params = inspect.signature(describe.statement).parameters
    assert list(params) == ["sql", "keep_literal"]
    assert _say(KG_SQL) == _say(KG_SQL)


def test_vocabulary_holds_the_fixed_words() -> None:
    for word in ("returns", "number", "rows", "total", "sorted", "descending", "first"):
        assert word in describe.VOCABULARY
