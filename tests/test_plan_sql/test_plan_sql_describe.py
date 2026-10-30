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
from typing import Any

import pytest
import sqlglot
from sqlglot import exp

from CortexOS.plan_sql import describe
from tests.test_plan_sql.test_c_loop_a_plan_sql import GOOD_SQL
from tests.test_plan_sql.test_plan_sql_vote import KG_SQL, SKU_SQL

CORPUS = Path(__file__).resolve().parents[1] / "test_execution" / "hostile_sql_corpus.json"


def _never(_text: str) -> bool:
    return False


# The granted payload the unit cases describe against: every table and column
# their SQL reads. A name outside it is refused (see the alias cases below).
_T_COLUMNS = ["a", "b", "c", "d", "j", "q", "x", "id", "qty", "flag", "kind"]
GRANTED: dict[str, list[str]] = {
    "t": _T_COLUMNS + [f"col_{i}" for i in range(200)],
    "u": ["a", "b", "c", "id", "kind"],
    "transactions": ["txn_type", "sku", "quantity_kg"],
}


def _say(sql: str, granted: dict[str, list[str]] | None = None) -> str:
    return describe.statement(
        sql, keep_literal=_never, granted=GRANTED if granted is None else granted
    )


def test_fixture_readings_are_distinct_and_name_their_aggregate() -> None:
    good, sku, kg = (_say(sql) for sql in (GOOD_SQL, SKU_SQL, KG_SQL))
    assert len({good, sku, kg}) == 3
    assert "number of rows" in good and "txn_type" in good
    # Was "sorted by n descending": ``n`` is the model's alias, so the reference
    # is shown as what it names (PICK-READING-01 review: an alias is a channel).
    assert "sorted by number of rows descending" in good
    assert "number of distinct sku" in sku
    assert "total of quantity_kg" in kg
    for text in (good, sku, kg):
        assert text.startswith("Returns ")
        assert "for each txn_type" in text and "from transactions" in text


# Was "the honest fallback": these rendered as "computed with 1 nested query",
# "window functions" and "combining 2 query results". PICK-READING-01 core
# grammar: the fallback is removed (it dropped WHERE and QUALIFY and merged
# nested scopes), so a query outside the core grammar is not offered.
@pytest.mark.parametrize(
    "sql",
    [
        "WITH x AS (SELECT a FROM t) SELECT a FROM x",
        "SELECT a FROM t WHERE b IN (SELECT b FROM u)",
        "SELECT a, ROW_NUMBER() OVER (ORDER BY b) AS r FROM t",
        "SELECT a FROM t UNION SELECT a FROM u",
    ],
    ids=["cte", "subquery", "window", "set_operation"],
)
def test_must_fail_complex_queries_refuse(sql: str) -> None:
    with pytest.raises(describe.DescribeError) as refused:
        _say(sql)
    assert "unparsable" not in str(refused.value)


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
    # In the core grammar: a CASE output repeated in GROUP BY or named by its
    # position, a NOT IN list, a CAST in a HAVING, a kept LIMIT next to a value.
    "group_by_case_output": (
        "SELECT CASE WHEN qty > 4711 THEN 'zq_big' ELSE 'zq_small' END AS g, COUNT(*) AS n "
        "FROM t GROUP BY CASE WHEN qty > 4711 THEN 'zq_big' ELSE 'zq_small' END"
    ),
    "order_by_case_ordinal": (
        "SELECT CASE WHEN qty > 4711 THEN 'zq_big' ELSE 'zq_small' END AS g FROM t ORDER BY 1"
    ),
    "not_in": "SELECT a FROM t WHERE c NOT IN ('zq_o', 'zq_p')",
    "having_cast": "SELECT a FROM t GROUP BY a HAVING MAX(d) > CAST('2019-01-02' AS DATE)",
    "limit_and_value": "SELECT a FROM t WHERE c = 'zq_q' LIMIT 3",
}
_SENTINEL = re.compile(r"zq_\w+|4711(?:\.5)?|4712|2019[-\d: ]*|'%Y'|%Y|-\s*<a value>")


# PICK-READING-01 core grammar: these rendered before (two through the removed
# nested-query fallback, the rest with a function, a CASE key, an INTERVAL, an
# OFFSET or a bare literal output printed as SQL text). Each is outside the core
# grammar now, so it refuses, which also prints no literal.
REDACT_REFUSED = {
    "cte",
    "subquery",
    "group_by_case",
    "order_by_case",
    "order_by_coalesce",
    "where_coalesce",
    "where_interval",
    "where_function_argument",
    "where_like",
    "unaliased_literal",
    "offset",
}


@pytest.mark.parametrize("case", sorted(REDACT))
def test_every_literal_in_every_clause_is_redacted(case: str) -> None:
    sql = REDACT[case]
    if case in REDACT_REFUSED:
        with pytest.raises(describe.DescribeError):
            _say(sql)
        return
    text = _say(sql)
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
    text = describe.statement(sql, keep_literal=lambda value: value == "zq_keep", granted=GRANTED)
    assert "zq_keep" in text and "zq_drop" not in text
    assert describe.MARK in text


def test_ordinals_name_the_projection_not_a_value() -> None:
    text = _say("SELECT txn_type, COUNT(*) AS n FROM transactions GROUP BY 1 ORDER BY 2 DESC")
    assert "for each txn_type" in text
    assert "sorted by number of rows descending" in text
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
    "projection_aggregate": "SELECT a, SUM(b /* zq_c10 */) AS k FROM t GROUP BY a",
    "where_comparison": "SELECT a FROM t WHERE b /* zq_c11 */ > 1 AND c IS NULL",
    "order_by": "SELECT a FROM t ORDER BY a /* zq_c12 */ DESC",
    "from_table": "SELECT a FROM t /* zq_c2 */",
    "where": "SELECT a FROM t WHERE COALESCE(b, c /* zq_c3 */) > 1 AND c IS NULL",
    "case": "SELECT CASE WHEN q /* zq_c4 */ > 1 THEN 'x' ELSE 'y' END AS k FROM t",
    "order_by_fallback": "SELECT a FROM t ORDER BY COALESCE(a, b) /* zq_c5 */",
    "inside_cte": "WITH x AS (SELECT a /* zq_c6 */ FROM t) SELECT a FROM x",
    "leading": "/* zq_c7 */ SELECT a FROM t",
    "trailing_line": "SELECT a FROM t -- zq_c8",
    "trailing_block": "SELECT a FROM t /* zq_c9 */",
}


# PICK-READING-01 core grammar: these rendered before; COALESCE and a CTE are
# outside the core grammar now, so they refuse (the in-grammar cases above
# keep the strip exercised).
COMMENTED_REFUSED = {"projection", "where", "order_by_fallback", "inside_cte"}


@pytest.mark.parametrize("case", sorted(COMMENTED))
def test_a_comment_never_reaches_the_statement(case: str) -> None:
    sql = COMMENTED[case]
    tree = sqlglot.parse_one(sql, read="duckdb")
    # The channel is real: sqlglot keeps the comment and prints it.
    assert "zq_c" in tree.sql(dialect="duckdb"), case
    if case in COMMENTED_REFUSED:
        with pytest.raises(describe.DescribeError):
            _say(sql)
        return
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


def _every_name_granted(sql: str) -> dict[str, list[str]]:
    """A payload that grants every table and column name the SQL spells.

    The corpus is about literals and comments, so its names are granted here
    to reach the literal paths; the alias cases below grant only the payload.
    """
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001 - describe refuses it too
        return {}
    names = sorted({c.name for c in tree.find_all(exp.Column) if c.name and c.name != "*"})
    return {t.name: names for t in tree.find_all(exp.Table) if t.name}


def test_hostile_corpus_never_prints_a_literal_or_a_comment() -> None:
    rendered = refused = 0
    for sql in _corpus():
        try:
            text = _say(sql, _every_name_granted(sql))
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
    # Was ``rendered >= 90``: the corpus is hostile SQL, and under the core
    # grammar (PICK-READING-01) 4 of its 106 cases render; the rest refuse.
    assert rendered >= 4 and refused >= 90, (rendered, refused)


# -- the cap and purity --------------------------------------------------------------


def test_cap_statement_bounds_the_text() -> None:
    assert describe.cap_statement("x" * 300) == "x" * 300
    capped = describe.cap_statement("x" * 1000)
    assert capped.endswith(" (shortened)")
    assert len(capped) == describe.STATEMENT_CAP + len(describe.SHORTENED) <= 312
    wide = ", ".join(f"col_{i}" for i in range(200))
    # Was rendered cut, ending in "(shortened)": a cut statement can end inside a
    # phrase ("is not" cut to "is"), so under the core grammar (PICK-READING-01)
    # a statement longer than the cap refuses instead.
    with pytest.raises(describe.DescribeError, match="too long"):
        _say(f"SELECT {wide} FROM t")
    fits = ", ".join(f"col_{i}" for i in range(30))
    assert len(_say(f"SELECT {fits} FROM t")) <= describe.STATEMENT_CAP


def test_statement_is_pure_and_never_sees_the_question() -> None:
    params = inspect.signature(describe.statement).parameters
    # ``granted`` (PICK-READING-01 review) is the payload's names, not the question.
    assert list(params) == ["sql", "keep_literal", "granted"]
    assert _say(KG_SQL) == _say(KG_SQL)


def test_vocabulary_holds_the_fixed_words() -> None:
    for word in ("returns", "number", "rows", "total", "sorted", "descending", "first"):
        assert word in describe.VOCABULARY


# -- PICK-READING-01 review: a model-chosen name is a value channel ---------------------

# Each case hides the sentinels zq_hi / zq_lo (the CASE labels of a bucket
# reading) in a name the model chose, never in a literal. Only names of the
# granted payload may be printed, so a sentinel in the output is a leak.
PAYLOAD_ONLY: dict[str, list[str]] = {"transactions": ["txn_type", "sku", "quantity_kg"]}
_BUCKET = "CASE WHEN quantity_kg > 100 THEN 'heavy' ELSE 'light' END"
NAMED = {
    "quoted_alias": f'SELECT {_BUCKET} AS "zq_hi or zq_lo", COUNT(*) AS n FROM transactions GROUP BY 1',
    "label_alias": (
        f"SELECT {_BUCKET} AS zq_hi, COUNT(*) AS zq_lo FROM transactions "
        "GROUP BY zq_hi ORDER BY zq_lo DESC"
    ),
    "alias_in_where": (
        f"SELECT {_BUCKET} AS zq_hi FROM transactions WHERE zq_hi IS NOT NULL ORDER BY zq_hi"
    ),
    "aggregate_alias": (
        "SELECT txn_type, SUM(quantity_kg) AS zq_hi FROM transactions GROUP BY txn_type "
        "HAVING zq_hi > 1 ORDER BY zq_hi"
    ),
    "renamed_column": "SELECT sku AS zq_hi FROM transactions ORDER BY zq_hi",
    "table_alias": (
        "SELECT CASE WHEN zq_hi.quantity_kg > 100 THEN 'heavy' ELSE 'light' END AS size "
        "FROM transactions AS zq_hi GROUP BY CASE WHEN zq_hi.quantity_kg > 100 "
        "THEN 'heavy' ELSE 'light' END"
    ),
    "self_join_aliases": (
        "SELECT zq_hi.sku, COUNT(*) FROM transactions AS zq_hi "
        "JOIN transactions AS zq_lo ON zq_hi.sku = zq_lo.sku GROUP BY zq_hi.sku"
    ),
    "using_join": "SELECT COUNT(*) FROM transactions AS zq_hi JOIN transactions AS zq_lo USING (sku)",
    "cte_name_mixed_case": (
        f"WITH Zq_hi AS (SELECT {_BUCKET} AS size FROM transactions) "
        "SELECT size, COUNT(*) AS n FROM ZQ_HI GROUP BY size"
    ),
    "cte_name_and_column": (
        f"WITH zq_hi AS (SELECT {_BUCKET} AS zq_lo FROM transactions) "
        "SELECT zq_lo, COUNT(*) AS n FROM zq_hi GROUP BY zq_lo"
    ),
    "cte_name_as_column": f"WITH zq_hi AS (SELECT {_BUCKET} AS b FROM transactions) SELECT zq_hi FROM zq_hi",
    "subquery_alias": (
        f"SELECT zq_lo AS zq_lo, COUNT(*) AS n FROM (SELECT {_BUCKET} AS zq_lo "
        "FROM transactions) AS zq_hi GROUP BY zq_lo"
    ),
    "union_alias": "SELECT sku AS zq_hi FROM transactions UNION SELECT txn_type FROM transactions",
    "ordinal_to_alias": f"SELECT {_BUCKET} AS zq_hi, COUNT(*) FROM transactions GROUP BY 1 ORDER BY 1",
}
_NAMED_SENTINEL = re.compile(r"zq_\w+", re.IGNORECASE)


def _words_are_granted_or_fixed(text: str, granted: dict[str, list[str]]) -> None:
    names = set(granted) | {c for cols in granted.values() for c in cols}
    sql_words = {"case", "when", "then", "else", "end", "count", "sum", "is", "not", "null"}
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.replace(describe.MARK, "")):
        assert word in names or word.lower() in describe.VOCABULARY | sql_words, (word, text)


@pytest.mark.parametrize("case", sorted(NAMED))
def test_a_model_chosen_name_never_reaches_the_statement(case: str) -> None:
    sql = NAMED[case]
    try:
        text = _say(sql, PAYLOAD_ONLY)
    except describe.DescribeError:
        return  # refusing is allowed: the offer is suppressed
    assert not _NAMED_SENTINEL.search(text), text
    for label in ("heavy", "light", "100"):
        assert label not in text, text
    _words_are_granted_or_fixed(text, PAYLOAD_ONLY)


# PICK-READING-01 core grammar: these rendered before (a self-join, USING, a
# CTE, a subquery and a UNION); each is outside the core grammar now. Round 4:
# label_alias (GROUP BY zq_hi) and alias_in_where (WHERE zq_hi ...) name a CASE
# output in a clause where DuckDB binds a real column first, and a column the
# payload does not list could carry that name, so they refuse too.
NAMED_REFUSED = {
    "label_alias",
    "alias_in_where",
    "self_join_aliases",
    "using_join",
    "cte_name_mixed_case",
    "cte_name_and_column",
    "cte_name_as_column",
    "subquery_alias",
    "union_alias",
}


def test_named_cases_render_without_their_names() -> None:
    """Not vacuous: the cases above render (refusing all would also pass them)."""
    for case in NAMED_REFUSED:
        with pytest.raises(describe.DescribeError):
            _say(NAMED[case], PAYLOAD_ONLY)
    rendered = {
        case: _say(sql, PAYLOAD_ONLY) for case, sql in NAMED.items() if case not in NAMED_REFUSED
    }
    # The CASE is stated in words now, not as SQL text.
    assert rendered["quoted_alias"].startswith(
        f"Returns computed value 1 (when quantity_kg is more than {describe.MARK} then "
    )
    assert "for each computed value 1" in rendered["quoted_alias"]
    assert "having total of quantity_kg is more than" in rendered["aggregate_alias"]
    assert "sorted by total of quantity_kg" in rendered["aggregate_alias"]
    assert rendered["renamed_column"] == "Returns sku from transactions, sorted by sku"
    assert "transactions.quantity_kg" in rendered["table_alias"]
    assert "for each computed value 1" in rendered["table_alias"]
    assert "for each computed value 1" in rendered["ordinal_to_alias"]
    assert "sorted by computed value 1" in rendered["ordinal_to_alias"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT sku FROM zq_heavy",
        "SELECT COUNT(*) FROM zq_heavy",
        "SELECT zq_heavy FROM transactions",
        "SELECT zq_heavy.sku FROM transactions",
        "SELECT transactions.zq_heavy FROM transactions",
        "SELECT sku FROM transactions AS t(zq_heavy, zq_light, q)",
        "SELECT list_transform([quantity_kg], zq_heavy -> zq_heavy + 1) FROM transactions",
        "SELECT {zq_heavy: quantity_kg} FROM transactions",
        "SELECT CAST(sku AS zq_heavy) FROM transactions",
        "SELECT zq_heavy_fn(sku) FROM transactions",
        "SELECT sku FROM transactions WHERE EXTRACT(zq_heavy FROM sku) > 1",
        "SELECT struct_pack(zq_heavy := sku) FROM transactions",
        "SELECT (sku AS zq_heavy) FROM transactions",
        "SELECT * REPLACE (sku AS zq_heavy) FROM transactions",
        "SELECT * RENAME (sku AS zq_heavy) FROM transactions",
        "SELECT sku FROM transactions LIMIT zq_heavy",
        "WITH x AS (SELECT sku FROM transactions) SELECT sku FROM x LIMIT zq_heavy",
        "WITH x AS (SELECT sku FROM transactions) SELECT sku FROM zq_heavy",
        "SELECT txn_type FROM transactions UNION SELECT sku FROM zq_heavy",
    ],
    ids=[
        "ungranted_table",
        "ungranted_table_count",
        "ungranted_column",
        "unknown_qualifier",
        "qualified_ungranted_column",
        "alias_renames_columns",
        "lambda_parameter",
        "struct_key",
        "user_defined_type",
        "unknown_function",
        "unknown_date_part",
        "named_argument",
        "nested_alias",
        "star_replace",
        "star_rename",
        "named_limit",
        "fallback_named_limit",
        "fallback_ungranted_table",
        "set_operation_ungranted_table",
    ],
)
def test_a_name_outside_the_payload_refuses(sql: str) -> None:
    sqlglot.parse_one(sql, read="duckdb")  # each case parses: it refuses on its name
    with pytest.raises(describe.DescribeError) as refused:
        _say(sql, PAYLOAD_ONLY)
    assert "unparsable" not in str(refused.value)


def test_an_empty_grant_names_nothing() -> None:
    with pytest.raises(describe.DescribeError):
        _say(GOOD_SQL, {})


def test_names_print_in_the_payload_spelling() -> None:
    """Case and quoting are the model's, so the payload's spelling is printed instead."""
    granted = {"Transactions": ["TxnType", "Sku"]}
    text = _say('SELECT "TXNTYPE", COUNT(DISTINCT sku) FROM TRANSACTIONS GROUP BY txntype', granted)
    assert text == "Returns TxnType and number of distinct Sku for each TxnType from Transactions"
    assert "TXNTYPE" not in text and "TRANSACTIONS" not in text
    # Was the fallback "Returns Sku from Transactions, computed with 1 nested
    # query": a CTE is outside the core grammar (PICK-READING-01), so it refuses.
    with pytest.raises(describe.DescribeError):
        _say("WITH x AS (SELECT SKU FROM TRANSACTIONS) SELECT SKU FROM x", granted)


def test_must_fail_a_table_function_source_refuses() -> None:
    """Was "Returns all columns" through the fallback: a source with no granted
    name is outside the core grammar (PICK-READING-01), so it refuses."""
    with pytest.raises(describe.DescribeError):
        _say("SELECT * FROM read_parquet('zq_path.parquet')", PAYLOAD_ONLY)


# -- PICK-READING-01 binding: a name is described as DuckDB binds it ---------------------

# A bare name that is both a granted column and a projection's alias. DuckDB binds
# it to the alias in ORDER BY (a whole key) and HAVING (not grouped), and to the
# column everywhere else. Each case: (SQL, the exact statement, the same query
# with the binding the statement claims spelled out, the same query with the
# other binding, which DuckDB either rejects or answers differently on this data).
# ``test_duckdb_binds_as_the_statement_says`` runs all three on real DuckDB, so a
# DuckDB that changed its rules fails here, not on a served answer. The first six
# were described as the raw column before this fix.
BIND_GRANTED: dict[str, list[str]] = {"t": ["k", "a", "b"]}
BIND_ROWS = "('x', 1, 60), ('x', 2, 70), ('y', 3, 50), ('z', 4, 90)"
BINDING: dict[str, tuple[str, str, str, str]] = {
    # Alias first: these were described as the raw column before the fix.
    "having_alias_of_total": (
        "SELECT k, SUM(b) AS b FROM t GROUP BY k HAVING b > 100",
        f"Returns k and total of b for each k from t having total of b is more than {describe.MARK}",
        "SELECT k, SUM(b) AS b FROM t GROUP BY k HAVING SUM(t.b) > 100",
        "SELECT k, SUM(b) AS b FROM t GROUP BY k HAVING t.b > 100",
    ),
    "having_alias_no_group_by": (
        "SELECT SUM(b) AS b FROM t HAVING b > 200",
        f"Returns total of b from t having total of b is more than {describe.MARK}",
        "SELECT SUM(b) AS b FROM t HAVING SUM(t.b) > 200",
        "SELECT SUM(b) AS b FROM t HAVING t.b > 200",
    ),
    "having_alias_not_grouped": (
        "SELECT a AS b, COUNT(*) AS n FROM t GROUP BY a HAVING b > 2",
        f"Returns a and number of rows for each a from t having a is more than {describe.MARK}",
        "SELECT a AS b, COUNT(*) AS n FROM t GROUP BY a HAVING t.a > 2",
        "SELECT a AS b, COUNT(*) AS n FROM t GROUP BY a HAVING t.b > 2",
    ),
    "order_alias_of_total": (
        "SELECT k, SUM(b) AS b FROM t GROUP BY k ORDER BY b DESC",
        "Returns k and total of b for each k from t, sorted by total of b descending",
        "SELECT k, SUM(b) AS b FROM t GROUP BY k ORDER BY SUM(t.b) DESC",
        "SELECT k, SUM(b) AS b FROM t GROUP BY k ORDER BY t.b DESC",
    ),
    "order_alias_swapped": (
        "SELECT a AS b, b AS a FROM t ORDER BY b",
        "Returns a and b from t, sorted by a",
        "SELECT a AS b, b AS a FROM t ORDER BY t.a",
        "SELECT a AS b, b AS a FROM t ORDER BY t.b",
    ),
    "order_alias_parenthesized": (
        "SELECT a AS b, b AS a FROM t ORDER BY ((b)) DESC",
        "Returns a and b from t, sorted by a descending",
        "SELECT a AS b, b AS a FROM t ORDER BY t.a DESC",
        "SELECT a AS b, b AS a FROM t ORDER BY t.b DESC",
    ),
    # Column first: the alias-first rule must not reach these.
    "having_grouped_column": (
        "SELECT a AS b, b AS a FROM t GROUP BY a, b HAVING b > 2",
        f"Returns a and b for each a and b from t having b is more than {describe.MARK}",
        "SELECT a AS b, b AS a FROM t GROUP BY a, b HAVING t.b > 2",
        "SELECT a AS b, b AS a FROM t GROUP BY a, b HAVING t.a > 2",
    ),
    "having_grouped_by_ordinal": (
        "SELECT a AS b, b AS a FROM t GROUP BY 1, 2 HAVING b > 2",
        f"Returns a and b for each a and b from t having b is more than {describe.MARK}",
        "SELECT a AS b, b AS a FROM t GROUP BY 1, 2 HAVING t.b > 2",
        "SELECT a AS b, b AS a FROM t GROUP BY 1, 2 HAVING t.a > 2",
    ),
    "having_inside_aggregate": (
        "SELECT k, SUM(a) AS b FROM t GROUP BY k HAVING MAX(b) > 65",
        f"Returns k and total of a for each k from t having largest b is more than {describe.MARK}",
        "SELECT k, SUM(a) AS b FROM t GROUP BY k HAVING MAX(t.b) > 65",
        "SELECT k, SUM(a) AS b FROM t GROUP BY k HAVING MAX(SUM(t.a)) > 65",
    ),
    "order_inside_aggregate": (
        "SELECT k, SUM(b) AS b FROM t GROUP BY k ORDER BY MAX(b) DESC",
        "Returns k and total of b for each k from t, sorted by largest b descending",
        "SELECT k, SUM(b) AS b FROM t GROUP BY k ORDER BY MAX(t.b) DESC",
        "SELECT k, SUM(b) AS b FROM t GROUP BY k ORDER BY SUM(t.b) DESC",
    ),
    "where_column": (
        "SELECT a AS b FROM t WHERE b > 55",
        f"Returns a from t where b is more than {describe.MARK}",
        "SELECT a AS b FROM t WHERE t.b > 55",
        "SELECT a AS b FROM t WHERE t.a > 55",
    ),
    "group_by_column": (
        "SELECT SUM(a) AS k, COUNT(*) AS n FROM t GROUP BY k",
        "Returns total of a and number of rows for each k from t",
        "SELECT SUM(a) AS k, COUNT(*) AS n FROM t GROUP BY t.k",
        "SELECT SUM(a) AS k, COUNT(*) AS n FROM t GROUP BY SUM(t.a)",
    ),
}


def _duckdb_rows(sql: str) -> list[tuple[Any, ...]] | str:
    """``sql`` on a scratch table ``t``: its rows in order, or the DuckDB error class."""
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE t (k VARCHAR, a INTEGER, b INTEGER)")
        con.execute(f"INSERT INTO t VALUES {BIND_ROWS}")
        try:
            return [tuple(row) for row in con.execute(sql).fetchall()]
        except duckdb.Error as exc:
            return type(exc).__name__
    finally:
        con.close()


# Round 4 (PICK-READING-01 review): an output alias that is not a plain
# passthrough, named like another name the statement reads, refuses. With a
# payload that lists every column these statements were true, but the payload
# may list a subset, and in a column-first clause DuckDB binds an unlisted
# column over the alias. So every BINDING case but these refuses now; DuckDB's
# rules are still pinned below on all of them.
BINDING_DESCRIBED = {"having_alias_not_grouped", "order_alias_unread", "having_alias_unread"}
BINDING.update(
    {
        # b is a listed column the statement reads nowhere else.
        "order_alias_unread": (
            "SELECT a AS b FROM t ORDER BY b DESC",
            "Returns a from t, sorted by a descending",
            "SELECT a AS b FROM t ORDER BY t.a DESC",
            "SELECT a AS b FROM t ORDER BY t.b DESC",
        ),
        "having_alias_unread": (
            "SELECT k, SUM(a) AS b FROM t GROUP BY k HAVING b > 2",
            f"Returns k and total of a for each k from t having total of a is more than "
            f"{describe.MARK}",
            "SELECT k, SUM(a) AS b FROM t GROUP BY k HAVING SUM(t.a) > 2",
            "SELECT k, SUM(a) AS b FROM t GROUP BY k HAVING t.b > 2",
        ),
    }
)


@pytest.mark.parametrize("case", sorted(BINDING_DESCRIBED))
def test_must_fail_a_shared_name_is_described_as_duckdb_binds_it(case: str) -> None:
    sql, expected, _claimed, _other = BINDING[case]
    assert _say(sql, BIND_GRANTED) == expected


@pytest.mark.parametrize("case", sorted(set(BINDING) - BINDING_DESCRIBED))
def test_must_fail_an_output_named_like_a_name_it_reads_refuses(case: str) -> None:
    """Was the BINDING statement (true only when the payload lists every column)."""
    sql, _expected, _claimed, _other = BINDING[case]
    with pytest.raises(describe.DescribeError, match="an output named like"):
        _say(sql, BIND_GRANTED)


@pytest.mark.parametrize("case", sorted(BINDING))
def test_duckdb_binds_as_the_statement_says(case: str) -> None:
    """The binding each statement claims (or claimed, before round 4) is the one
    DuckDB uses, on this data."""
    sql, _expected, claimed, other = BINDING[case]
    rows = _duckdb_rows(sql)
    assert isinstance(rows, list), rows
    assert rows == _duckdb_rows(claimed)
    assert _duckdb_rows(other) != rows, "the data must tell the two bindings apart"


# Where DuckDB's binding is not one the statement can state, it refuses.
UNBOUND_NAMES: dict[str, str] = {
    # DuckDB takes the last of two outputs that share a name.
    "two_outputs_one_name_order": "SELECT a AS x, b AS x FROM t ORDER BY x",
    "two_outputs_one_name_having": (
        "SELECT k, SUM(a) AS x, SUM(b) AS x FROM t GROUP BY k HAVING x > 5"
    ),
    "two_outputs_shadow_column": "SELECT a AS b, k AS b FROM t ORDER BY b",
    # GROUP BY ALL binds a HAVING name to the alias, unlike a listed GROUP BY (the
    # leaf check also refuses GROUP BY ALL); ROLLUP and expressions are not resolved.
    "group_by_all_having": "SELECT a AS b, b AS a FROM t GROUP BY ALL HAVING b > 2",
    "rollup_having": "SELECT a AS b, b AS a FROM t GROUP BY ROLLUP (a, b) HAVING b > 2",
    "grouping_expression_having": "SELECT a + 0 AS b, b AS a FROM t GROUP BY a + 0, b HAVING b > 2",
    # Clauses the statement does not describe.
    "qualify": "SELECT a FROM t QUALIFY a > 1",
    # PICK-READING-01 core grammar: these three were BINDING cases that rendered
    # (an aggregate FILTER, ``b + 0`` as a sort key and as an output, each
    # printed as SQL text). Arithmetic and FILTER are outside the core grammar.
    "having_inside_aggregate_filter": (
        "SELECT k, SUM(a) AS b FROM t GROUP BY k HAVING COUNT(*) FILTER (WHERE b > 55) > 1"
    ),
    "order_part_of_expression": "SELECT a AS b, b AS a FROM t ORDER BY b + 0",
    "select_list_column": "SELECT a AS b, b + 0 AS c FROM t",
    "distinct_on": "SELECT DISTINCT ON (k) k, a FROM t ORDER BY k, a",
}


@pytest.mark.parametrize("case", sorted(UNBOUND_NAMES))
def test_must_fail_an_unresolved_binding_refuses(case: str) -> None:
    with pytest.raises(describe.DescribeError):
        _say(UNBOUND_NAMES[case], BIND_GRANTED)


def test_duckdb_takes_the_last_of_two_outputs_that_share_a_name() -> None:
    """Why two outputs with one name refuse: DuckDB binds the second, a first-wins
    rule would describe the first, and nothing promises either stays true."""
    assert _duckdb_rows(UNBOUND_NAMES["two_outputs_one_name_order"]) == _duckdb_rows(
        "SELECT a AS x, b AS x FROM t ORDER BY t.b"
    )
    assert _duckdb_rows(UNBOUND_NAMES["two_outputs_one_name_order"]) != _duckdb_rows(
        "SELECT a AS x, b AS x FROM t ORDER BY t.a"
    )


def test_duckdb_group_by_all_binds_having_to_the_alias() -> None:
    """Why GROUP BY ALL refuses: it does not follow the listed-GROUP BY rule."""
    listed = _duckdb_rows("SELECT a AS b, b AS a FROM t GROUP BY a, b HAVING b > 2")
    group_all = _duckdb_rows(UNBOUND_NAMES["group_by_all_having"])
    assert sorted(group_all) == sorted(
        _duckdb_rows("SELECT a AS b, b AS a FROM t GROUP BY ALL HAVING t.a > 2")
    )
    assert sorted(listed) != sorted(group_all)
