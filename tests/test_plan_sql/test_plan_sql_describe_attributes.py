"""PICK-READING-01 review: a statement states every attribute of every node it reads.

``plan_sql.describe`` rendered by node type. sqlglot parses ``x NOT LIKE p`` as
``Like(negate=True)`` and ``ANTI JOIN`` as ``Join(kind=ANTI)``; the renderer read
neither, so it said "matches" and "matched to": the inverse of the query. A
picked reading is served with ``reading: <statement>`` as its first
assumption, so a statement that drops an attribute is a false claim on a
served answer (WRONG=0).

The fix is a strict attribute audit: every node class the renderer reads is
declared with exactly the args (and values) it states, and anything else
refuses. This file holds:

* the statements of each construct the renderer supports, each checked on real
  DuckDB against the reading it claims and the reading it must not claim;
* must-fail refusals for each construct it does not state exactly;
* a property test over the hostile corpus and a generated set: every attribute
  a statement depends on changes the statement when removed (an oracle that
  does not read describe's declarations), every node it reads carries only
  declared attributes, and every undeclared attribute injected into a tree
  refuses;
* a static check that every node class the renderer names is declared.
"""

from __future__ import annotations

import ast
import inspect
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlglot
from sqlglot import exp

from CortexOS.plan_sql import describe

CORPUS = Path(__file__).resolve().parents[1] / "test_execution" / "hostile_sql_corpus.json"
MARK = describe.MARK

GRANTED: dict[str, list[str]] = {"t": ["k", "a", "b", "c"], "u": ["k", "a", "c", "flag"]}
T_ROWS = (
    "('x', 1, 60, 'SKU-1'), ('x', 2, NULL, 'SKU-2'), ('y', 3, 50, 'OTHER'), "
    "('z', NULL, 90, NULL), ('w', 5, 70, 'sku-3')"
)
U_ROWS = (
    "('x', 1, 'SKU-1', TRUE), ('y', 3, 'OTHER', FALSE), ('q', 9, 'SKU-9', TRUE), "
    "('x', 1, 'SKU-1', TRUE)"
)


def _never(_text: str) -> bool:
    return False


def _say(sql: str) -> str:
    return describe.statement(sql, keep_literal=_never, granted=GRANTED)


def _duckdb_rows(sql: str) -> list[tuple[Any, ...]] | str:
    """``sql`` on scratch tables ``t`` and ``u``: rows in order, or the DuckDB error class."""
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    try:
        con.execute("SET threads TO 1")
        con.execute("CREATE TABLE t (k VARCHAR, a INTEGER, b INTEGER, c VARCHAR)")
        con.execute(f"INSERT INTO t VALUES {T_ROWS}")
        con.execute("CREATE TABLE u (k VARCHAR, a INTEGER, c VARCHAR, flag BOOLEAN)")
        con.execute(f"INSERT INTO u VALUES {U_ROWS}")
        try:
            return [tuple(row) for row in con.execute(sql).fetchall()]
        except duckdb.Error as exc:
            return type(exc).__name__
    finally:
        con.close()


def _bag(rows: list[tuple[Any, ...]] | str) -> list[tuple[str, ...]] | str:
    """Rows as a sorted multiset, for results whose order the SQL does not fix."""
    if isinstance(rows, str):
        return rows
    return sorted(tuple(repr(v) for v in row) for row in rows)


# -- 1. every supported construct, stated and checked on DuckDB -----------------------

# Each case: (SQL, its exact statement, the SQL that spells out what the
# statement says, the SQL of the reading the statement must not be confused
# with, whether row order is part of the claim). The last two must return
# different rows on the data above, or the case proves nothing.
CONSTRUCTS: dict[str, tuple[str, str, str, str, bool]] = {
    "not_like": (
        "SELECT k FROM t WHERE c NOT LIKE 'SKU%'",
        f"Returns k from t where c does not match {MARK}",
        "SELECT k FROM t WHERE NOT (c LIKE 'SKU%')",
        "SELECT k FROM t WHERE c LIKE 'SKU%'",
        False,
    ),
    "not_like_operator": (
        "SELECT k FROM t WHERE c !~~ 'SKU%'",
        f"Returns k from t where c does not match {MARK}",
        "SELECT k FROM t WHERE NOT (c LIKE 'SKU%')",
        "SELECT k FROM t WHERE c LIKE 'SKU%'",
        False,
    ),
    "not_ilike": (
        "SELECT k FROM t WHERE c NOT ILIKE 'sku%'",
        f"Returns k from t where c does not match, ignoring case, {MARK}",
        "SELECT k FROM t WHERE NOT (c ILIKE 'sku%')",
        "SELECT k FROM t WHERE c ILIKE 'sku%'",
        False,
    ),
    "not_ilike_operator": (
        "SELECT k FROM t WHERE c !~~* 'sku%'",
        f"Returns k from t where c does not match, ignoring case, {MARK}",
        "SELECT k FROM t WHERE NOT (c ILIKE 'sku%')",
        "SELECT k FROM t WHERE c ILIKE 'sku%'",
        False,
    ),
    "like_control": (
        "SELECT k FROM t WHERE c LIKE 'SKU%'",
        f"Returns k from t where c matches {MARK}",
        "SELECT k FROM t WHERE c LIKE 'SKU%'",
        "SELECT k FROM t WHERE NOT (c LIKE 'SKU%')",
        False,
    ),
    "anti_join": (
        "SELECT t.k FROM t ANTI JOIN u ON t.k = u.k",
        "Returns t.k from t that have no match in u on t.k is u.k",
        "SELECT t.k FROM t WHERE NOT EXISTS (SELECT 1 FROM u WHERE t.k = u.k)",
        "SELECT t.k FROM t JOIN u ON t.k = u.k",
        False,
    ),
    "semi_join": (
        "SELECT t.k FROM t SEMI JOIN u ON t.k = u.k",
        "Returns t.k from t that have a match in u on t.k is u.k",
        "SELECT t.k FROM t WHERE EXISTS (SELECT 1 FROM u WHERE t.k = u.k)",
        "SELECT t.k FROM t JOIN u ON t.k = u.k",
        False,
    ),
    "anti_join_using": (
        "SELECT k FROM t ANTI JOIN u USING (k)",
        "Returns k from t that have no match in u on k",
        "SELECT k FROM t WHERE NOT EXISTS (SELECT 1 FROM u WHERE t.k = u.k)",
        "SELECT t.k FROM t JOIN u USING (k)",
        False,
    ),
    "left_join": (
        "SELECT t.k, u.a FROM t LEFT JOIN u ON t.k = u.k",
        "Returns t.k and u.a from t, matched to u on t.k is u.k, keeping rows of t with no match",
        "SELECT t.k, u.a FROM t JOIN u ON t.k = u.k UNION ALL "
        "SELECT t.k, NULL FROM t WHERE NOT EXISTS (SELECT 1 FROM u WHERE t.k = u.k)",
        "SELECT t.k, u.a FROM t JOIN u ON t.k = u.k",
        False,
    ),
    "left_outer_join": (
        "SELECT t.k, u.a FROM t LEFT OUTER JOIN u ON t.k = u.k",
        "Returns t.k and u.a from t, matched to u on t.k is u.k, keeping rows of t with no match",
        "SELECT t.k, u.a FROM t JOIN u ON t.k = u.k UNION ALL "
        "SELECT t.k, NULL FROM t WHERE NOT EXISTS (SELECT 1 FROM u WHERE t.k = u.k)",
        "SELECT t.k, u.a FROM t JOIN u ON t.k = u.k",
        False,
    ),
    "inner_join_control": (
        "SELECT t.k, u.a FROM t INNER JOIN u ON t.k = u.k",
        "Returns t.k and u.a from t, matched to u on t.k is u.k",
        "SELECT t.k, u.a FROM t, u WHERE t.k = u.k",
        "SELECT t.k, u.a FROM t LEFT JOIN u ON t.k = u.k",
        False,
    ),
    "comma_join": (
        "SELECT t.k, u.k FROM t, u",
        "Returns t.k and u.k from t, paired with every row of u",
        "SELECT t.k, u.k FROM t CROSS JOIN u",
        "SELECT t.k, u.k FROM t JOIN u ON t.k = u.k",
        False,
    ),
    "cross_join": (
        "SELECT t.k, u.k FROM t CROSS JOIN u",
        "Returns t.k and u.k from t, paired with every row of u",
        "SELECT t.k, u.k FROM t, u",
        "SELECT t.k, u.k FROM t JOIN u ON t.k = u.k",
        False,
    ),
    "positional_join": (
        "SELECT t.k, u.k FROM t POSITIONAL JOIN u",
        "Returns t.k and u.k from t, paired by position with u, "
        "padding the shorter one with empty values",
        "SELECT l.k, r.k FROM (SELECT k, row_number() OVER () AS p FROM t) AS l "
        "FULL JOIN (SELECT k, row_number() OVER () AS p FROM u) AS r ON l.p = r.p",
        "SELECT t.k, u.k FROM t, u",
        False,
    ),
    "nulls_first": (
        "SELECT k, b FROM t ORDER BY b NULLS FIRST",
        "Returns k and b from t, sorted by b with empty values first",
        "SELECT k, b FROM t ORDER BY (b IS NULL) DESC, b",
        "SELECT k, b FROM t ORDER BY b",
        True,
    ),
    "desc_nulls_first": (
        "SELECT k, b FROM t ORDER BY b DESC NULLS FIRST",
        "Returns k and b from t, sorted by b descending with empty values first",
        "SELECT k, b FROM t ORDER BY (b IS NULL) DESC, b DESC",
        "SELECT k, b FROM t ORDER BY b DESC",
        True,
    ),
    # Silence is DuckDB's default: empty values last in both directions, so an
    # explicit NULLS LAST says nothing more and is not stated.
    "desc_default_nulls_last": (
        "SELECT k, b FROM t ORDER BY b DESC",
        "Returns k and b from t, sorted by b descending",
        "SELECT k, b FROM t ORDER BY (b IS NULL), b DESC",
        "SELECT k, b FROM t ORDER BY b DESC NULLS FIRST",
        True,
    ),
    "asc_nulls_last": (
        "SELECT k, b FROM t ORDER BY b NULLS LAST",
        "Returns k and b from t, sorted by b",
        "SELECT k, b FROM t ORDER BY (b IS NULL), b",
        "SELECT k, b FROM t ORDER BY b NULLS FIRST",
        True,
    ),
    "except": (
        "SELECT k FROM t EXCEPT SELECT k FROM u",
        "Returns k from t and u, keeping distinct rows of the first query result "
        "that are not in the second",
        "SELECT DISTINCT k FROM t WHERE NOT EXISTS "
        "(SELECT 1 FROM u WHERE u.k IS NOT DISTINCT FROM t.k)",
        "SELECT k FROM t UNION SELECT k FROM u",
        False,
    ),
    "intersect": (
        "SELECT k FROM t INTERSECT SELECT k FROM u",
        "Returns k from t and u, keeping distinct rows found in both query results",
        "SELECT DISTINCT k FROM t WHERE EXISTS "
        "(SELECT 1 FROM u WHERE u.k IS NOT DISTINCT FROM t.k)",
        "SELECT k FROM t UNION SELECT k FROM u",
        False,
    ),
    "union_by_name": (
        "SELECT k, c FROM t UNION ALL BY NAME SELECT c, k FROM u",
        "Returns k and c from t and u, combining 2 query results, matching columns by name",
        "SELECT k, c FROM t UNION ALL SELECT k, c FROM u",
        "SELECT k, c FROM t UNION ALL SELECT c, k FROM u",
        False,
    ),
}


@pytest.mark.parametrize("case", sorted(CONSTRUCTS))
def test_must_fail_each_construct_is_stated_exactly(case: str) -> None:
    sql, expected, _claimed, _other, _ordered = CONSTRUCTS[case]
    assert _say(sql) == expected


@pytest.mark.parametrize("case", sorted(CONSTRUCTS))
def test_duckdb_runs_each_construct_as_its_statement_says(case: str) -> None:
    """The reading each statement claims is the one DuckDB runs, on this data."""
    sql, _expected, claimed, other, ordered = CONSTRUCTS[case]
    view = (lambda rows: rows) if ordered else _bag
    rows = _duckdb_rows(sql)
    assert isinstance(rows, list), rows
    assert view(rows) == view(_duckdb_rows(claimed))
    assert view(_duckdb_rows(other)) != view(rows), "the data must tell the readings apart"


# A nested query's output keeps its name only if it reads that granted column
# unchanged: ``SELECT k FROM (SELECT c AS k FROM t)`` returns c, so naming it
# "k" would be false. Each: (SQL, exact statement, SQL the granted name claims).
RENAMED_THROUGH: dict[str, tuple[str, str, str]] = {
    "subquery_renames": (
        "SELECT k FROM (SELECT c AS k FROM t) AS s",
        "Returns computed value 1 from t, computed with 1 nested query",
        "SELECT k FROM t",
    ),
    "cte_renames": (
        "WITH s AS (SELECT c AS k FROM t) SELECT k FROM s",
        "Returns computed value 1 from t, computed with 1 nested query",
        "SELECT k FROM t",
    ),
    "union_mixes_columns": (
        "SELECT k FROM t UNION ALL SELECT c FROM u",
        "Returns computed value 1 from t and u, combining 2 query results",
        "SELECT k FROM t UNION ALL SELECT k FROM u",
    ),
}


@pytest.mark.parametrize("case", sorted(RENAMED_THROUGH))
def test_must_fail_a_renamed_output_is_not_named_as_the_column(case: str) -> None:
    sql, expected, named = RENAMED_THROUGH[case]
    assert _say(sql) == expected
    assert _bag(_duckdb_rows(sql)) != _bag(_duckdb_rows(named)), "the name would be false"


def test_an_unrenamed_nested_output_keeps_its_name() -> None:
    sql = "SELECT k FROM (SELECT k, a FROM t WHERE a > 1) AS s"
    assert _say(sql) == "Returns k from t, computed with 1 nested query"
    star = "WITH s AS (SELECT * FROM t) SELECT x.k FROM s AS x"
    assert _say(star) == "Returns k from t, computed with 1 nested query"


# -- 2. what is not stated exactly refuses ---------------------------------------------

REFUSED: dict[str, str] = {
    "right_join": "SELECT t.k FROM t RIGHT JOIN u ON t.k = u.k",
    "full_join": "SELECT t.k FROM t FULL JOIN u ON t.k = u.k",
    "asof_join": "SELECT t.k FROM t ASOF JOIN u ON t.a >= u.a",
    "asof_left_join": "SELECT t.k FROM t ASOF LEFT JOIN u ON t.a >= u.a",
    "natural_join": "SELECT t.k FROM t NATURAL JOIN u",
    "left_semi_join": "SELECT t.k FROM t LEFT SEMI JOIN u ON t.k = u.k",
    "left_anti_join": "SELECT t.k FROM t LEFT ANTI JOIN u ON t.k = u.k",
    "left_join_then_join": ("SELECT t.k FROM t LEFT JOIN u ON t.k = u.k JOIN u AS v ON u.a = v.a"),
    "join_then_left_join": ("SELECT t.k FROM t JOIN u ON t.k = u.k LEFT JOIN u AS v ON u.a = v.a"),
    "positional_then_join": "SELECT t.k FROM t POSITIONAL JOIN u JOIN u AS v ON u.a = v.a",
    "star_exclude": "SELECT * EXCLUDE (a) FROM t",
    "qualified_star_exclude": "SELECT t.* EXCLUDE (a) FROM t",
    "limit_percent": "SELECT k FROM t LIMIT 40%",
    "fetch_first": "SELECT k FROM t FETCH FIRST 2 ROWS ONLY",
    "rollup": "SELECT k, COUNT(*) FROM t GROUP BY ROLLUP (k)",
    "cube": "SELECT k, COUNT(*) FROM t GROUP BY CUBE (k)",
    "grouping_sets": "SELECT k, COUNT(*) FROM t GROUP BY GROUPING SETS ((k), ())",
    "group_by_all": "SELECT k, COUNT(*) FROM t GROUP BY ALL",
    "star_then_ordinal_order": "SELECT *, b FROM t ORDER BY 2",
    "star_then_ordinal_group": "SELECT *, COUNT(*) FROM t GROUP BY 1, 2, 3, 4",
    "qualified_star_then_ordinal": "SELECT t.*, u.a FROM t JOIN u ON t.k = u.k ORDER BY 2",
    "columns_then_ordinal": "SELECT COLUMNS('a|b'), k FROM t ORDER BY 2",
    "star_then_alias_reference": "SELECT *, a + 1 AS z FROM t ORDER BY z",
    "between_symmetric": "SELECT k FROM t WHERE a BETWEEN SYMMETRIC 3 AND 1",
    "max_n": "SELECT MAX(a, 2) FROM t",
    "min_n": "SELECT MIN(a, 2) FROM t",
    "schema_qualified_table": "SELECT k FROM other.t",
    "main_qualified_table": "SELECT k FROM main.t",
    "catalog_qualified_table": "SELECT k FROM memory.main.t",
    "tablesample": "SELECT k FROM t TABLESAMPLE 50%",
    "using_sample": "SELECT k FROM t USING SAMPLE 50%",
    "time_travel": "SELECT k FROM t AT (VERSION => 1)",
    "relation_renames_columns": "SELECT k FROM (SELECT c, k FROM t) AS s(k, c)",
    "cte_renames_columns": "WITH s(k) AS (SELECT c FROM t) SELECT k FROM s",
    "except_all": "SELECT k FROM t EXCEPT ALL SELECT k FROM u",
    "intersect_all": "SELECT k FROM t INTERSECT ALL SELECT k FROM u",
    "except_in_subquery": "SELECT k FROM (SELECT k FROM t EXCEPT SELECT k FROM u) AS s",
    "except_then_union": "SELECT k FROM t EXCEPT SELECT k FROM u UNION SELECT k FROM t",
    "by_name_mixed": "SELECT k FROM t UNION BY NAME SELECT k FROM u UNION SELECT k FROM t",
    "union_fetch": "SELECT k FROM t UNION SELECT k FROM u FETCH FIRST 2 ROWS ONLY",
    "union_limit_percent": "SELECT k FROM t UNION SELECT k FROM u LIMIT 40%",
    "fallback_star_exclude": "SELECT * EXCLUDE (a) FROM t UNION SELECT * EXCLUDE (a) FROM t",
    "fallback_schema_table": "WITH s AS (SELECT k FROM other.t) SELECT k FROM s",
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_must_fail_an_attribute_not_stated_refuses(case: str) -> None:
    sql = REFUSED[case]
    sqlglot.parse_one(sql, read="duckdb")  # each case parses: it refuses on its attribute
    with pytest.raises(describe.DescribeError) as refused:
        _say(sql)
    assert "unparsable" not in str(refused.value)


# Why a star refuses an ordinal or an output reference: DuckDB counts the
# star's columns, so position 2 is t.a, not the b the list shows second.
def test_duckdb_counts_a_star_s_columns_in_an_ordinal() -> None:
    star = _duckdb_rows(REFUSED["star_then_ordinal_order"])
    assert star == _duckdb_rows("SELECT *, b FROM t ORDER BY t.a")
    assert star != _duckdb_rows("SELECT *, b FROM t ORDER BY t.b")


def test_duckdb_rollup_adds_rows_for_each_does_not_state() -> None:
    rollup = _duckdb_rows(REFUSED["rollup"])
    plain = _duckdb_rows("SELECT k, COUNT(*) FROM t GROUP BY k")
    assert isinstance(rollup, list) and isinstance(plain, list)
    assert len(rollup) == len(plain) + 1


def test_duckdb_limit_percent_is_not_a_row_count() -> None:
    assert len(_duckdb_rows("SELECT k FROM t LIMIT 40%")) == 2
    assert len(_duckdb_rows("SELECT k FROM t LIMIT 40")) == 5


def test_duckdb_star_exclude_is_not_all_columns() -> None:
    assert len(_duckdb_rows(REFUSED["star_exclude"])[0]) == 3
    assert len(_duckdb_rows("SELECT * FROM t")[0]) == 4


# -- 3. the property: every attribute is declared and stated, or refuses -----------------


def _corpus() -> list[str]:
    return [case["sql"] for case in json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]]


def _generated() -> list[str]:
    """SQL over ``t`` and ``u`` that uses every node class and attribute the renderer
    reads, each varied one clause at a time, plus the forms it must refuse."""
    predicates = [
        "c LIKE 'p%'",
        "c NOT LIKE 'p%'",
        "c ILIKE 'p%'",
        "c NOT ILIKE 'p%'",
        "c ~~ 'p%'",
        "c !~~ 'p%'",
        "c ~~* 'p%'",
        "c !~~* 'p%'",
        "c LIKE 'p!%' ESCAPE '!'",
        "c LIKE ANY ('p%', 'q%')",
        "NOT c LIKE 'p%'",
        "a = 1",
        "a <> 1",
        "a > 1",
        "a >= 1",
        "a < 1",
        "a <= 1",
        "a IN (1, 2)",
        "a NOT IN (1, 2)",
        "a BETWEEN 1 AND 2",
        "a NOT BETWEEN 1 AND 2",
        "a BETWEEN SYMMETRIC 2 AND 1",
        "c IS NULL",
        "c IS NOT NULL",
        "a IS DISTINCT FROM 1",
        "a IS NOT DISTINCT FROM 1",
        "c SIMILAR TO 'p.*'",
        "c GLOB 'p*'",
        "regexp_matches(c, 'p')",
        "a = 1 AND b = 2",
        "a = 1 OR b = 2",
        "(a = 1 OR b = 2) AND c IS NULL",
        "NOT (a = 1)",
        "flag",
        "NOT flag",
        "a IN (SELECT a FROM u)",
        "EXISTS (SELECT 1 FROM u)",
        "-a < -1",
        "a > EXTRACT(year FROM CURRENT_DATE)",
        "a IS TRUE",
        "a IN (b, 1 + 1)",
    ]
    joins = [
        "JOIN u ON t.k = u.k",
        "INNER JOIN u ON t.k = u.k",
        "LEFT JOIN u ON t.k = u.k",
        "LEFT OUTER JOIN u ON t.k = u.k",
        "RIGHT JOIN u ON t.k = u.k",
        "FULL JOIN u ON t.k = u.k",
        "FULL OUTER JOIN u ON t.k = u.k",
        "SEMI JOIN u ON t.k = u.k",
        "ANTI JOIN u ON t.k = u.k",
        "LEFT SEMI JOIN u ON t.k = u.k",
        "LEFT ANTI JOIN u ON t.k = u.k",
        "ASOF JOIN u ON t.a >= u.a",
        "ASOF LEFT JOIN u ON t.a >= u.a",
        "NATURAL JOIN u",
        "POSITIONAL JOIN u",
        "CROSS JOIN u",
        ", u",
        "JOIN u USING (k)",
        "ANTI JOIN u USING (k)",
        "SEMI JOIN u USING (k)",
        "LEFT JOIN u USING (k)",
        "JOIN u AS v ON t.k = v.k",
        "JOIN u ON t.k = u.k JOIN u AS v ON u.a = v.a",
        "ANTI JOIN u ON t.k = u.k JOIN u AS v ON t.a = v.a",
        "LEFT JOIN u ON t.k = u.k JOIN u AS v ON t.a = v.a",
    ]
    projections = [
        "k",
        "t.k",
        "*",
        "t.*",
        "* EXCLUDE (a)",
        "* REPLACE (a + 1 AS a)",
        "COUNT(*)",
        "COUNT(a)",
        "COUNT(DISTINCT a)",
        "SUM(a)",
        "SUM(DISTINCT a)",
        "AVG(a)",
        "MIN(a)",
        "MAX(a)",
        "MAX(a, 2)",
        "MIN(a, 2)",
        "a + 1",
        "DISTINCT k",
        "COLUMNS('a|b')",
        "UNNEST([1, 2])",
        "CASE WHEN a > 1 THEN 'p' ELSE 'q' END",
        "k, COUNT(*) AS n",
        "a AS b, b AS a",
        "COALESCE(c, 'p')",
        "-a",
    ]
    tails = [
        "GROUP BY k",
        "GROUP BY 1",
        "GROUP BY ALL",
        "GROUP BY ROLLUP (k)",
        "GROUP BY CUBE (k)",
        "GROUP BY GROUPING SETS ((k), ())",
        "GROUP BY k HAVING COUNT(*) > 1",
        "ORDER BY k",
        "ORDER BY k DESC",
        "ORDER BY k NULLS FIRST",
        "ORDER BY k NULLS LAST",
        "ORDER BY k DESC NULLS FIRST",
        "ORDER BY k DESC NULLS LAST",
        "ORDER BY 1",
        "ORDER BY ALL",
        "ORDER BY k COLLATE NOCASE",
        "LIMIT 5",
        "LIMIT 5 OFFSET 2",
        "OFFSET 2",
        "LIMIT 40%",
        "FETCH FIRST 3 ROWS ONLY",
        "QUALIFY a > 1",
        "ORDER BY k LIMIT 5",
        "GROUP BY k ORDER BY COUNT(*) DESC NULLS FIRST LIMIT 3",
    ]
    tables = [
        "t",
        "main.t",
        "other.t",
        "memory.main.t",
        "t AS x",
        "t AS x(p, q, r, s)",
        "t TABLESAMPLE 50%",
        "t USING SAMPLE 50%",
        "t AT (VERSION => 1)",
        "(SELECT k, a, b, c FROM t) AS s",
        "(SELECT c AS k, a, b, c FROM t) AS s",
    ]
    sets = [
        "SELECT k FROM t UNION SELECT k FROM u",
        "SELECT k FROM t UNION ALL SELECT k FROM u",
        "SELECT k FROM t EXCEPT SELECT k FROM u",
        "SELECT k FROM t EXCEPT ALL SELECT k FROM u",
        "SELECT k FROM t INTERSECT SELECT k FROM u",
        "SELECT k FROM t INTERSECT ALL SELECT k FROM u",
        "SELECT k, c FROM t UNION BY NAME SELECT c, k FROM u",
        "SELECT k, c FROM t UNION ALL BY NAME SELECT c, k FROM u",
        "SELECT k FROM t UNION SELECT k FROM u UNION ALL SELECT k FROM t",
        "SELECT k FROM t EXCEPT SELECT k FROM u UNION SELECT k FROM t",
        "SELECT k FROM t UNION SELECT c FROM u",
        "SELECT * FROM t UNION SELECT * EXCLUDE (flag) FROM u",
        "SELECT k FROM t UNION SELECT k FROM u ORDER BY k LIMIT 2",
        "SELECT k FROM t UNION SELECT k FROM u LIMIT 40%",
        "SELECT k FROM t UNION SELECT k FROM u ORDER BY k LIMIT 2 OFFSET 1",
        "SELECT k FROM t EXCEPT SELECT k FROM u ORDER BY k LIMIT 2 OFFSET 1",
        "SELECT k FROM t INTERSECT SELECT k FROM u ORDER BY k LIMIT 2 OFFSET 1",
        "WITH s AS (SELECT k FROM t) SELECT k FROM s UNION SELECT k FROM u",
        "WITH s AS (SELECT k FROM t) SELECT k FROM s EXCEPT SELECT k FROM u",
        "WITH s AS (SELECT k FROM t) SELECT k FROM s INTERSECT SELECT k FROM u",
        "WITH s AS (SELECT k FROM t) SELECT k FROM s",
        "WITH s AS (SELECT c AS k FROM t) SELECT k FROM s",
        "WITH s(k) AS (SELECT c FROM t) SELECT k FROM s",
        "WITH RECURSIVE s AS (SELECT 1 AS k UNION ALL SELECT k + 1 FROM s WHERE k < 3) "
        "SELECT k FROM s",
        "SELECT k, ROW_NUMBER() OVER (ORDER BY a DESC NULLS FIRST) AS r FROM t",
        "SELECT k FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY k ORDER BY a) = 1",
        "SELECT DISTINCT ON (k) k, a FROM t ORDER BY k, a",
    ]
    out = [f"SELECT k FROM t WHERE {p}" for p in predicates]
    out += [f"SELECT t.k FROM t {j}" for j in joins]
    out += [f"SELECT {p} FROM t" for p in projections]
    out += [f"SELECT k, COUNT(*) FROM t {tail}" for tail in tails if "GROUP" in tail]
    out += [f"SELECT k, a FROM t {tail}" for tail in tails if "GROUP" not in tail]
    out += [f"SELECT k FROM {table}" for table in tables]
    out += sets
    out += [f"SELECT k FROM t WHERE {p} ORDER BY a DESC NULLS FIRST" for p in predicates[:12]]
    out += [f"SELECT t.k FROM t {j} WHERE t.c NOT LIKE 'p%'" for j in joins[:12]]
    return out


GENERATED = _generated()


def _universe() -> Iterator[str]:
    yield from _corpus()
    yield from GENERATED


def _granted_for(sql: str) -> dict[str, list[str]]:
    """Every table and column name the SQL spells, granted: the property is about
    attributes, so names never stand between a tree and the renderer."""
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001 - describe refuses it too
        return {}
    names = sorted({c.name for c in tree.find_all(exp.Column) if c.name and c.name != "*"})
    names += [i.name for i in tree.find_all(exp.Identifier) if i.name]
    return {t.name: sorted(set(names)) for t in tree.find_all(exp.Table) if t.name}


def _describe(sql: str, granted: dict[str, list[str]]) -> str | None:
    try:
        return describe.statement(sql, keep_literal=_never, granted=granted)
    except describe.DescribeError:
        return None


def _parsed(sql: str) -> exp.Expression | None:
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001
        return None
    if tree is None:
        return None
    for node in tree.walk():
        node.comments = None
    return tree


def _carried(value: object) -> bool:
    return value is not None and value is not False and value != [] and value != ""


def _read_nodes(tree: exp.Expression) -> list[exp.Expression]:
    """The nodes whose attributes the statement depends on, chosen by the path the
    renderer takes, not by describe's declarations."""
    if isinstance(tree, exp.Select) and describe._simple(tree):
        return list(tree.walk())
    read: list[exp.Expression] = []
    for node in tree.walk():
        if isinstance(node, (exp.Table, exp.TableAlias, exp.SetOperation)):
            read.append(node)
    for key in ("limit", "offset"):
        value = tree.args.get(key)
        if isinstance(value, exp.Expression):
            read.append(value)
    for select in describe._output_selects(tree):
        for projection in select.expressions:
            # An output is named by its column, or by position: only these are read.
            inner = projection.this if isinstance(projection, exp.Alias) else projection
            read.extend(
                n for n in (projection, inner) if isinstance(n, (exp.Alias, exp.Column, exp.Star))
            )
            if isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star):
                read.append(inner.this)
    return read


# Removing these does not change what the query returns, or the statement is
# true of both values by design; each says why.
MEANING_PRESERVED: dict[tuple[type[exp.Expression], str], str] = {
    (exp.Alias, "alias"): "an output's alias is never printed (step 5)",
    (exp.Table, "alias"): "a relation alias is never printed; references resolve or refuse",
    (exp.TableAlias, "this"): "a relation alias is never printed",
    (exp.Count, "big_int"): "a sqlglot spelling flag, not SQL meaning",
    (exp.Identifier, "quoted"): "names print in the payload's spelling",
    (
        exp.Identifier,
        "this",
    ): "a name, checked against the grant (step 5); an alias's is never printed",
    (exp.Union, "distinct"): "'combining N query results' is true with or without duplicates",
    (exp.Union, "order"): "the fallback states no order; 'first N rows' is true for any",
    (exp.Except, "order"): "the fallback states no order; 'first N rows' is true for any",
    (exp.Intersect, "order"): "the fallback states no order; 'first N rows' is true for any",
    (exp.Window, "alias"): "a window alias is never printed",
}
# Value-specific equivalences: removing kind INNER, OUTER (under LEFT) or
# CROSS gives the same join.
SAME_JOIN = {("kind", "INNER"), ("kind", "OUTER"), ("kind", "CROSS")}


def _meaning_preserved(node: exp.Expression, key: str, value: object, fallback: bool) -> bool:
    if (type(node), key) in MEANING_PRESERVED:
        return True
    if isinstance(node, exp.Join) and (key, str(value).upper()) in SAME_JOIN:
        return True
    if isinstance(node, exp.Count) and key == "this" and isinstance(value, exp.Star):
        return True  # COUNT() is COUNT(*) in DuckDB
    if fallback and isinstance(node, exp.Column) and key == "table":
        return True  # the fallback names an output by its column, never its qualifier
    return isinstance(node, describe._VALUE_TYPES)  # a value is redacted by design


def _paths(tree: exp.Expression) -> dict[int, tuple[Any, ...]]:
    """id(node) -> the arg path from the root, to find the node again in a copy."""
    paths: dict[int, tuple[Any, ...]] = {id(tree): ()}
    for node in tree.walk():
        for key, value in node.args.items():
            if isinstance(value, exp.Expression):
                paths[id(value)] = (*paths[id(node)], key)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, exp.Expression):
                        paths[id(item)] = (*paths[id(node)], key, index)
    return paths


def _follow(tree: exp.Expression, path: tuple[Any, ...]) -> exp.Expression:
    node: Any = tree
    for step in path:
        node = node[step] if isinstance(step, int) else node.args[step]
    return node


def _without(tree: exp.Expression, path: tuple[Any, ...], key: str) -> str | None:
    copy = tree.copy()
    node = _follow(copy, path)
    node.set(key, None)
    try:
        return copy.sql(dialect="duckdb")
    except Exception:  # noqa: BLE001 - not SQL any more: nothing to compare
        return None


def test_property_every_attribute_a_statement_reads_changes_it_or_refuses() -> None:
    """The oracle reads only sqlglot and the statement, never describe's tables.

    For every SQL that describes, every carried attribute of every node the
    statement depends on is removed in turn. If the SQL changes, the statement
    must change too or refuse: a statement that stays the same has dropped
    that attribute. A renderer that ignored ``negate`` or a join's kind fails
    here on ``NOT LIKE`` and ``ANTI JOIN``.
    """
    checked = described = 0
    silent: list[tuple[str, str, str, str]] = []
    for sql in _universe():
        granted = _granted_for(sql)
        tree = _parsed(sql)
        if tree is None:
            continue
        base_sql = tree.sql(dialect="duckdb")
        base = _describe(base_sql, granted)
        if base is None or base.endswith(describe.SHORTENED) or "..." in base:
            continue
        described += 1
        fallback = not (isinstance(tree, exp.Select) and describe._simple(tree))
        paths = _paths(tree)
        for node in _read_nodes(tree):
            for key, value in list(node.args.items()):
                # An attribute is a flag or keyword (``negate``, ``nulls_first``,
                # a join's kind), or an optional clause; removing a required
                # sub-expression leaves no SQL to compare.
                structural = isinstance(value, (exp.Expression, list))
                if not _carried(value) or (structural and node.arg_types.get(key, True)):
                    continue
                if _meaning_preserved(node, key, value, fallback):
                    continue
                changed = _without(tree, paths[id(node)], key)
                if changed is None or changed == base_sql:
                    continue
                checked += 1
                after = _describe(changed, granted)
                if after is not None and after == base:
                    silent.append((sql, type(node).__name__, key, base))
    assert not silent, silent[:5]
    assert described >= 200 and checked >= 800, (described, checked)


def _declared(node: exp.Expression, simple: bool) -> bool:
    table = (
        describe._HANDLED
        if simple or not isinstance(node, exp.SetOperation)
        else (describe._SET_OPERATIONS)
    )
    declared = table.get(type(node))
    if declared is None:
        return False
    return all(
        key in declared and declared[key](value)
        for key, value in node.args.items()
        if _carried(value)
    )


def test_property_every_node_a_statement_reads_carries_only_declared_attributes() -> None:
    """Wiring: no statement is rendered from a node whose attributes are not declared."""
    described = 0
    for sql in _universe():
        tree = _parsed(sql)
        if tree is None or _describe(sql, _granted_for(sql)) is None:
            continue
        described += 1
        simple = isinstance(tree, exp.Select) and describe._simple(tree)
        for node in _read_nodes(tree):
            if simple and not isinstance(node, describe._READ_FAMILIES):
                continue
            if not simple and not isinstance(
                node,
                (
                    exp.Table,
                    exp.TableAlias,
                    exp.SetOperation,
                    exp.Limit,
                    exp.Offset,
                    exp.Alias,
                    exp.Column,
                    exp.Star,
                ),
            ):
                continue
            assert _declared(node, simple), (sql, type(node).__name__, node.args)
    assert described >= 150, described


def _carriers() -> dict[type[exp.Expression], list[tuple[str, exp.Expression, tuple[Any, ...]]]]:
    """For each read class: SQL that describes and the path of a node of that class."""
    found: dict[type[exp.Expression], list[tuple[str, exp.Expression, tuple[Any, ...]]]] = {}
    for sql in GENERATED:
        tree = _parsed(sql)
        if tree is None or _describe(sql, _granted_for(sql)) is None:
            continue
        paths = _paths(tree)
        for node in _read_nodes(tree):
            found.setdefault(type(node), []).append((sql, tree, paths[id(node)]))
    return found


# Args sqlglot defines that DuckDB SQL cannot produce; each is still injected.
INJECTED = "zq_injected"


def test_property_every_undeclared_attribute_refuses_when_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every arg sqlglot defines on a read class is declared, or refuses.

    A tree that describes gets one undeclared arg (or a value no declaration
    accepts) set on one of its read nodes; ``statement`` must raise
    DescribeError, not render and not crash. New args a later sqlglot adds to
    these classes are covered without editing this test.
    """
    carriers = _carriers()
    tables = {**describe._HANDLED, **describe._SET_OPERATIONS}
    missing: list[str] = []
    injected = 0
    for cls, declared in tables.items():
        if cls not in carriers:
            missing.append(cls.__name__)
            continue
        sql, tree, path = carriers[cls][0]
        granted = _granted_for(sql)
        for key in cls.arg_types:
            accepts_anything = key in declared and declared[key] is describe._any
            if accepts_anything:
                continue
            copy = tree.copy()
            _follow(copy, path).set(key, INJECTED)
            with monkeypatch.context() as patch:
                patch.setattr(describe.sqlglot, "parse_one", lambda *_a, _t=copy, **_k: _t.copy())
                with pytest.raises(describe.DescribeError):
                    describe.statement(sql, keep_literal=_never, granted=granted)
            injected += 1
    assert not missing, f"no generated SQL describes a node of: {missing}"
    assert injected >= 150, injected


def test_generated_sql_reaches_every_declared_attribute() -> None:
    """Not vacuous: each declared arg is carried in some SQL that describes, so the
    sensitivity property above exercised it; the gate admits the generated SQL."""
    reached: set[tuple[type[exp.Expression], str]] = set()
    for sql in GENERATED:
        tree = _parsed(sql)
        if tree is None or _describe(sql, _granted_for(sql)) is None:
            continue
        for node in _read_nodes(tree):
            for key, value in node.args.items():
                if _carried(value):
                    reached.add((type(node), key))
    tables = {**describe._HANDLED, **describe._SET_OPERATIONS}
    # The arg names an older sqlglot used for ``with_`` and ``from_``: declared so
    # an older sqlglot is read the same way, never carried on this one.
    unreachable = {
        (exp.Select, "from"),
        (exp.Union, "with"),
        (exp.Except, "with"),
        (exp.Intersect, "with"),
    }
    not_reached = sorted(
        f"{cls.__name__}.{key}"
        for cls, declared in tables.items()
        for key in declared
        if (cls, key) not in reached and (cls, key) not in unreachable
    )
    assert not not_reached, not_reached


def test_the_gate_admits_the_supported_constructs() -> None:
    """The generated constructs the renderer states are ones the SQL gate admits,
    so the property covers SQL that can reach a served answer."""
    from CortexOS.execution.manifest import (
        Manifest,
        ManifestError,
        VerifiedManifest,
        enforce_manifest,
    )

    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="sess-1",
        org_id="acme",
        pool_id="pool-a",
        issuer_key_id="int-1",
        allowed_paths=[],
        row_predicates={"t": "TRUE", "u": "TRUE"},
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    verified = VerifiedManifest(manifest=manifest, issuer_kid="int-1", verified_at=now)
    admitted: set[type[exp.Expression]] = set()
    for sql in GENERATED:
        try:
            enforce_manifest(sql, verified)
        except ManifestError:
            continue
        tree = _parsed(sql)
        if tree is not None and _describe(sql, _granted_for(sql)) is not None:
            admitted.update(type(node) for node in _read_nodes(tree))
    for case in CONSTRUCTS.values():
        enforce_manifest(case[0], verified)  # each supported construct passes the gate
    declared = set(describe._HANDLED) | set(describe._SET_OPERATIONS)
    assert declared <= admitted | {exp.Var, exp.Paren, exp.TableAlias}, sorted(
        c.__name__ for c in declared - admitted
    )


# -- 4. the renderer names only declared classes -----------------------------------------

# Classes the rendering code names that it does not read for meaning: it only
# refuses on them, counts them for a marker, or checks them as leaves.
NOT_READ_FOR_MEANING = {
    "Anonymous",  # refused in a row count; a leaf the fallback alone allows
    "Identifier",  # a leaf: names are rewritten to granted text first
    "Query",  # refused in a row count
    "Subquery",  # counted: "computed with N nested queries"
    "CTE",  # counted, and its name is never printed
    "Window",  # counted: "window functions"
    "Lateral",  # routes to the fallback
    "SetOperation",  # read through _SET_OPERATIONS by exact class
    "Expression",  # type annotations
    "Mapping",
}
RENDERERS = (
    "_render_select",
    "_from_text",
    "_condition",
    "_operand",
    "_projection",
    "_aggregate",
    "_column",
    "_limit_text",
    "_count",
    "_table",
    "_reference",
    "_set_phrase",
    "_output_name",
    "_output_names",
    "_output_selects",
    "_branches",
    "_render_fallback",
    "_relations",
    "_derived",
    "_reads_column",
    "_query_reads",
    "_join_shape",
    "_star_of",
)


def _named_classes() -> set[str]:
    source = inspect.getsource(describe)
    module = ast.parse(source)
    names: set[str] = set()
    for node in module.body:
        target = None
        if isinstance(node, ast.FunctionDef) and node.name in RENDERERS:
            target = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(t, ast.Name) and t.id in {"_COMPARISONS", "_AGGREGATES"} for t in targets
            ):
                target = node
        if target is None:
            continue
        for sub in ast.walk(target):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "exp"
                and sub.attr[:1].isupper()
            ):
                names.add(sub.attr)
    return names


def test_must_fail_every_class_the_renderer_reads_is_declared() -> None:
    """Removing a class from ``_HANDLED`` while the renderer still reads it fails here."""
    declared = {cls.__name__ for cls in (*describe._HANDLED, *describe._SET_OPERATIONS)}
    named = _named_classes()
    assert {"Like", "Join", "Ordered", "Star", "Limit"} <= named  # the scan sees the renderer
    undeclared = sorted(named - declared - NOT_READ_FOR_MEANING)
    assert not undeclared, undeclared


def test_vocabulary_holds_the_new_words() -> None:
    for word in ("does", "match", "paired", "keeping", "have", "values", "position", "second"):
        assert word in describe.VOCABULARY, word


def test_statements_use_only_vocabulary_and_granted_names() -> None:
    names = set(GRANTED) | {c for cols in GRANTED.values() for c in cols}
    for sql, *_rest in CONSTRUCTS.values():
        text = _say(sql).replace(MARK, "")
        for word in text.replace(",", " ").replace(".", " ").split():
            assert word in names or word.isdigit() or word.lower() in describe.VOCABULARY, (
                word,
                text,
            )
