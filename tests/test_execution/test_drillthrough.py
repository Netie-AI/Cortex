"""T7 drill-through rewrite + token tests (no DuckDB required for rewrite)."""

from __future__ import annotations

import time

import pytest

from CortexOS.execution.drillthrough import (
    DrillthroughTokenInvalid,
    mint_token,
    rewrite_for_drillthrough,
    sql_digest,
    verify_token,
)


def test_rewrite_strips_sum_keeps_where_and_join():
    sql = (
        "SELECT SUM(s.amount) AS total "
        "FROM silver.sales s "
        "JOIN silver.customer c ON c.id = s.customer_id "
        "WHERE s.quarter = 'Q3' AND c.region = 'Northern'"
    )
    out = rewrite_for_drillthrough(sql)
    low = out.sql.lower()
    assert "sum(" not in low
    assert "s.amount" in low or "amount" in low
    assert "silver.sales" in low
    assert "silver.customer" in low
    assert "quarter" in low and "northern" in low
    assert "limit 5000" in low
    assert "_src_ref_id" in low
    assert out.measure_expr is not None
    assert "count(" in out.count_sql.lower()


def test_rewrite_drops_group_by():
    sql = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region"
    out = rewrite_for_drillthrough(sql)
    assert "group by" not in out.sql.lower()


def test_token_roundtrip_and_expiry(monkeypatch):
    monkeypatch.setenv("CORTEX_DRILLTHROUGH_HMAC", "test-secret")
    tok = mint_token(
        answer_id="ans_1",
        session_id="sess_1",
        manifest_hash="abc",
        sql="SELECT 1",
        expires_at=time.time() + 60,
    )
    payload = verify_token(tok)
    assert payload["answer_id"] == "ans_1"
    assert payload["sql_digest"] == sql_digest("SELECT 1")

    expired = mint_token(
        answer_id="ans_1",
        session_id="sess_1",
        manifest_hash="abc",
        sql="SELECT 1",
        expires_at=time.time() - 1,
    )
    with pytest.raises(DrillthroughTokenInvalid, match="expired"):
        verify_token(expired)


def test_token_tamper_fails(monkeypatch):
    monkeypatch.setenv("CORTEX_DRILLTHROUGH_HMAC", "test-secret")
    tok = mint_token(
        answer_id="ans_1",
        session_id="sess_1",
        manifest_hash="abc",
        sql="SELECT 1",
    )
    bad = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    with pytest.raises(DrillthroughTokenInvalid):
        verify_token(bad)


def test_rewrite_group_by_count_star_projects_no_star_and_drops_group_order():
    sql = "SELECT txn_type, COUNT(*) AS n FROM transactions GROUP BY txn_type ORDER BY n DESC"
    out = rewrite_for_drillthrough(sql, provenance_for=lambda schema, table: [])
    assert out.sql == "SELECT txn_type FROM transactions LIMIT 5000"
    assert out.approximate is False
    assert out.measure_expr is None


def test_rewrite_count_distinct_drills_to_the_counted_column():
    out = rewrite_for_drillthrough(
        "SELECT COUNT(DISTINCT sku) AS skus FROM transactions",
        provenance_for=lambda schema, table: [],
    )
    assert out.sql == "SELECT sku AS skus FROM transactions ORDER BY sku DESC LIMIT 5000"


def test_rewrite_projects_only_provenance_columns_the_table_has():
    seen: list[tuple[str | None, str]] = []

    def lookup(schema, table):  # noqa: ANN001
        seen.append((schema, table))
        return ["_src_ref_id", "_ingest_id"] if table == "sales" else []

    out = rewrite_for_drillthrough(
        "SELECT SUM(s.amount) AS total FROM silver.sales s "
        "JOIN silver.customer c ON c.id = s.customer_id",
        provenance_for=lookup,
    )
    assert seen == [("silver", "sales"), ("silver", "customer")]
    low = out.sql.lower()
    assert "s._src_ref_id as s__src_ref_id" in low
    assert "s._ingest_id as s__ingest_id" in low
    assert "_src_row" not in low
    assert "c._src" not in low


def test_rewrite_unparseable_sql_is_a_drillthrough_error():
    from CortexOS.execution.drillthrough import DrillthroughError

    with pytest.raises(DrillthroughError):
        rewrite_for_drillthrough("SELECT FROM WHERE (")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, COUNT(*) AS n FROM sales GROUP BY region OFFSET 1",
        "SELECT region, COUNT(*) AS n FROM sales GROUP BY region LIMIT 0",
        "SELECT SUM(amount) AS t FROM sales LIMIT 1 OFFSET 1",
        "SELECT ROUND(SUM(amount), 2) AS t FROM sales",
        "SELECT SUM(amount) / COUNT(*) AS t FROM sales",
        "SELECT COUNT(*) FILTER (WHERE region = 'N') AS n FROM sales",
        "SELECT region, ROUND(AVG(amount), 1) AS a FROM sales GROUP BY region",
    ],
)
def test_rewrite_refuses_shapes_whose_rows_it_cannot_derive(sql):
    from CortexOS.execution.drillthrough import DrillthroughError

    with pytest.raises(DrillthroughError):
        rewrite_for_drillthrough(sql, provenance_for=lambda schema, table: [])


def test_rewrite_keeps_limit_on_a_one_row_aggregate():
    out = rewrite_for_drillthrough(
        "SELECT SUM(amount) AS t FROM sales LIMIT 1", provenance_for=lambda schema, table: []
    )
    assert out.sql == "SELECT amount AS t FROM sales ORDER BY amount DESC LIMIT 5000"


def test_rewrite_keeps_order_and_limit_of_a_row_answer():
    out = rewrite_for_drillthrough(
        "SELECT id, amount FROM sales ORDER BY amount DESC LIMIT 10 OFFSET 2",
        provenance_for=lambda schema, table: [],
    )
    assert out.sql == "SELECT id, amount FROM sales ORDER BY amount DESC LIMIT 10 OFFSET 2"
    assert out.approximate is False
    assert out.count_sql == (
        "SELECT COUNT(*) AS contributing_count FROM "
        "(SELECT id, amount FROM sales ORDER BY amount DESC LIMIT 10 OFFSET 2) AS _answer"
    )


def test_rewrite_caps_a_row_answer_limit_at_the_drill_limit():
    out = rewrite_for_drillthrough(
        "SELECT id FROM sales ORDER BY id LIMIT 99999", provenance_for=lambda schema, table: []
    )
    assert out.sql == "SELECT id FROM sales ORDER BY id LIMIT 5000"


def test_rewrite_leaves_window_aggregates_alone():
    out = rewrite_for_drillthrough(
        "SELECT region, SUM(amount) OVER (PARTITION BY region) AS t FROM sales",
        provenance_for=lambda schema, table: [],
    )
    assert out.sql == (
        "SELECT region, SUM(amount) OVER (PARTITION BY region) AS t FROM sales LIMIT 5000"
    )


def test_rewrite_counts_groups_when_a_limit_may_drop_some():
    sql = "SELECT region, SUM(amount) AS t FROM sales GROUP BY region ORDER BY t DESC LIMIT 3"
    out = rewrite_for_drillthrough(sql, provenance_for=lambda schema, table: [])
    assert out.group_limit == 3
    assert out.group_count_sql == (
        "SELECT COUNT(*) AS group_count FROM "
        "(SELECT region, SUM(amount) AS t FROM sales GROUP BY region) AS _groups"
    )
    plain = rewrite_for_drillthrough(
        "SELECT region, SUM(amount) AS t FROM sales GROUP BY region",
        provenance_for=lambda schema, table: [],
    )
    assert plain.group_count_sql is None and plain.group_limit is None


def test_rewrite_drops_qualify_and_counts_the_groups_it_kept():
    sql = (
        "SELECT region, SUM(amount) AS t FROM sales GROUP BY region "
        "QUALIFY ROW_NUMBER() OVER (ORDER BY region) <= 2"
    )
    out = rewrite_for_drillthrough(sql, provenance_for=lambda schema, table: [])
    assert "QUALIFY" not in out.sql.upper()
    assert "QUALIFY" not in out.count_sql.upper()
    assert out.group_count_sql == (
        "SELECT COUNT(*) AS group_count FROM "
        "(SELECT region, SUM(amount) AS t FROM sales GROUP BY region) AS _groups"
    )
    assert out.kept_count_sql == (
        "SELECT COUNT(*) AS kept_count FROM "
        "(SELECT region, SUM(amount) AS t FROM sales GROUP BY region "
        "QUALIFY ROW_NUMBER() OVER (ORDER BY region) <= 2) AS _kept"
    )


def _no_prov(schema, table):  # noqa: ANN001, ANN202
    return []


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT ARRAY_AGG(txn_id ORDER BY qty DESC) AS ids FROM t WHERE k = 'W'",
            "SELECT txn_id AS ids FROM t WHERE k = 'W' ORDER BY qty DESC LIMIT 5000",
        ),
        (
            "SELECT k, STRING_AGG(txn_id, ', ' ORDER BY txn_id) AS ids FROM t GROUP BY k",
            "SELECT k, txn_id AS ids FROM t ORDER BY txn_id LIMIT 5000",
        ),
        (
            "SELECT STRING_AGG(DISTINCT k, ',' ORDER BY k) AS ks FROM t",
            "SELECT k AS ks FROM t ORDER BY k LIMIT 5000",
        ),
        (
            "SELECT FIRST(txn_id ORDER BY qty DESC, txn_id) AS v FROM t",
            "SELECT txn_id AS v FROM t ORDER BY qty DESC, txn_id LIMIT 5000",
        ),
        (
            "SELECT LIST(txn_id ORDER BY txn_id) AS v, SUM(qty) AS s FROM t",
            "SELECT txn_id AS v, qty AS s FROM t ORDER BY txn_id LIMIT 5000",
        ),
        (
            "SELECT SUM(qty ORDER BY txn_id) AS s FROM t",
            "SELECT qty AS s FROM t ORDER BY txn_id LIMIT 5000",
        ),
        (
            "SELECT ANY_VALUE(txn_id) AS v FROM t",
            "SELECT txn_id AS v FROM t ORDER BY txn_id DESC LIMIT 5000",
        ),
        (
            "SELECT FIRST(txn_id IGNORE NULLS) AS v FROM t",
            "SELECT txn_id AS v FROM t ORDER BY txn_id DESC LIMIT 5000",
        ),
        (
            "SELECT MEAN(qty) AS m FROM t WHERE k = 'W'",
            "SELECT qty AS m FROM t WHERE k = 'W' ORDER BY qty DESC LIMIT 5000",
        ),
        (
            "SELECT k, PRODUCT(qty) AS p FROM t GROUP BY k",
            "SELECT k, qty AS p FROM t ORDER BY qty DESC LIMIT 5000",
        ),
        (
            "SELECT k, COUNT(1) AS n FROM t GROUP BY k ORDER BY n DESC",
            "SELECT k, 1 AS n FROM t LIMIT 5000",
        ),
    ],
)
def test_rewrite_builds_the_drill_from_aggregate_nodes(sql, expected):
    """Ordered, null-modified and DuckDB-only aggregates drill to their per-row argument."""
    out = rewrite_for_drillthrough(sql, provenance_for=_no_prov)
    assert out.sql == expected
    assert out.approximate is False


def test_rewrite_refuses_an_aggregate_that_limits_its_own_input():
    from CortexOS.execution.drillthrough import DrillthroughError

    with pytest.raises(DrillthroughError, match="limits its own input"):
        rewrite_for_drillthrough(
            "SELECT ARRAY_AGG(txn_id ORDER BY qty DESC LIMIT 3) AS ids FROM t",
            provenance_for=_no_prov,
        )


def test_rewrite_count_star_alone_drills_to_provenance_when_the_table_has_it():
    sql = "SELECT COUNT(*) AS n FROM t WHERE k = 'W'"
    out = rewrite_for_drillthrough(sql, provenance_for=lambda schema, table: ["_src_ref_id"])
    assert out.sql == "SELECT t._src_ref_id AS t__src_ref_id FROM t WHERE k = 'W' LIMIT 5000"
    from CortexOS.execution.drillthrough import DrillthroughError

    with pytest.raises(DrillthroughError, match="no projectable columns"):
        rewrite_for_drillthrough(sql, provenance_for=_no_prov)


def test_rewrite_turns_a_sqlglot_failure_while_building_into_a_drillthrough_error(monkeypatch):
    import sqlglot

    from CortexOS.execution import drillthrough
    from CortexOS.execution.drillthrough import DrillthroughError

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise sqlglot.errors.ParseError("cannot build")

    monkeypatch.setattr(drillthrough, "_provenance_projections", boom)
    with pytest.raises(DrillthroughError, match="could not be built"):
        rewrite_for_drillthrough("SELECT SUM(qty) AS s FROM t")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ARRAY_AGG(STRUCT_PACK(a := txn_id) ORDER BY txn_id) AS v FROM t",
        "SELECT ARRAY_AGG(txn_id ORDER BY qty DESC NULLS LAST, txn_id) AS v FROM t",
        "SELECT ARRAY_AGG(DISTINCT txn_id ORDER BY txn_id) AS v FROM t",
        "SELECT LISTAGG(txn_id, ',') WITHIN GROUP (ORDER BY txn_id) AS v FROM t",
        "SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY qty) AS v FROM t",
        "SELECT ARRAY_AGG(txn_id ORDER BY qty) FILTER (WHERE k = 'W') AS v FROM t",
        "SELECT ARRAY_AGG(txn_id ORDER BY qty)[1] AS v FROM t",
        "SELECT COUNT(t.*) AS n FROM t",
        "SELECT HISTOGRAM(k) AS h FROM t",
        "SELECT k, ARG_MAX(txn_id, qty) AS v FROM t GROUP BY ALL",
        "WITH x AS (SELECT * FROM t) SELECT LIST(txn_id ORDER BY qty) AS v FROM x",
        "SELECT k FROM t UNION SELECT k FROM u",
    ],
)
def test_rewrite_of_any_aggregate_shape_is_a_rewrite_or_a_drillthrough_error(sql):
    """Never another exception type: the route maps only DrillthroughError to a 4xx."""
    from CortexOS.execution.drillthrough import DrillRewrite, DrillthroughError

    try:
        out = rewrite_for_drillthrough(sql, provenance_for=_no_prov)
    except DrillthroughError:
        return
    assert isinstance(out, DrillRewrite)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT MAX(qty, 3) AS top3 FROM t",
            "SELECT qty AS top3 FROM t ORDER BY qty DESC LIMIT 5000",
        ),
        (
            "SELECT k, MIN(qty, 2) AS low2 FROM t GROUP BY k",
            "SELECT k, qty AS low2 FROM t ORDER BY qty DESC LIMIT 5000",
        ),
        (
            "SELECT ARG_MAX(txn_id, qty, 2) AS v FROM t",
            "SELECT txn_id AS v FROM t ORDER BY txn_id DESC LIMIT 5000",
        ),
        (
            "SELECT GEOMEAN(qty) AS g FROM t WHERE k = 'W'",
            "SELECT qty AS g FROM t WHERE k = 'W' ORDER BY qty DESC LIMIT 5000",
        ),
        (
            "SELECT k, GEOMETRIC_MEAN(qty) AS g FROM t GROUP BY k",
            "SELECT k, qty AS g FROM t ORDER BY qty DESC LIMIT 5000",
        ),
        (
            "SELECT WEIGHTED_AVG(qty, w) AS a FROM t",
            "SELECT qty AS a FROM t ORDER BY qty DESC LIMIT 5000",
        ),
        (
            "SELECT WAVG(qty, w) AS a FROM t",
            "SELECT qty AS a FROM t ORDER BY qty DESC LIMIT 5000",
        ),
        (
            "SELECT JSON_GROUP_ARRAY(txn_id) AS ids FROM t",
            "SELECT txn_id AS ids FROM t ORDER BY txn_id DESC LIMIT 5000",
        ),
        (
            "SELECT k, JSON_GROUP_OBJECT(txn_id, qty) AS o FROM t GROUP BY k",
            "SELECT k, txn_id AS o FROM t ORDER BY txn_id DESC LIMIT 5000",
        ),
        (
            "SELECT JSON_GROUP_STRUCTURE(doc) AS s FROM t",
            "SELECT doc AS s FROM t ORDER BY doc DESC LIMIT 5000",
        ),
        (
            "SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY qty) AS m FROM t",
            "SELECT qty AS m FROM t ORDER BY qty LIMIT 5000",
        ),
        (
            "SELECT k, MODE() WITHIN GROUP (ORDER BY txn_id) AS m FROM t GROUP BY k",
            "SELECT k, txn_id AS m FROM t ORDER BY txn_id LIMIT 5000",
        ),
    ],
    ids=[
        "max-n",
        "min-n",
        "arg_max-n",
        "geomean",
        "geometric_mean",
        "weighted_avg",
        "wavg",
        "json_group_array",
        "json_group_object",
        "json_group_structure",
        "percentile-within-group",
        "mode-within-group",
    ],
)
def test_rewrite_projects_the_aggregates_per_row_argument_not_a_parameter(sql, expected):
    """Macro aggregates are aggregates; MAX(x, n) drills x, never the literal n."""
    out = rewrite_for_drillthrough(sql, provenance_for=_no_prov)
    assert out.sql == expected
    assert out.approximate is False


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("SELECT ARG_MAX(3, qty) AS v FROM t", "which argument"),
        ("SELECT CORR(0.5, qty) AS v FROM t", "which argument"),
        ("SELECT JSON_GROUP_OBJECT(3, qty) AS v FROM t", "which argument"),
        ("SELECT MY_EXTENSION_AGG(qty) AS v FROM t", "cannot tell whether MY_EXTENSION_AGG"),
        ("SELECT k, MY_EXTENSION_AGG(qty) AS v FROM t GROUP BY k", "cannot tell whether"),
        ("SELECT (SELECT MAX(qty) FROM t) AS m", "reads rows from a table"),
        ("SELECT (SELECT MAX(qty) FROM t) AS m FROM t LIMIT 1", "computed by a subquery"),
        (
            "SELECT k, SUM(qty) AS s, (SELECT SUM(qty) FROM t) AS total FROM t GROUP BY k",
            "window or subquery total",
        ),
        (
            "SELECT k, COUNT(*) AS n, ROW_NUMBER() OVER (ORDER BY k) AS rn FROM t GROUP BY k",
            "window or subquery total",
        ),
    ],
)
def test_rewrite_refuses_shapes_that_would_serve_values_not_in_the_rows(sql, reason):
    from CortexOS.execution.drillthrough import DrillthroughError

    with pytest.raises(DrillthroughError, match=reason):
        rewrite_for_drillthrough(sql, provenance_for=_no_prov)


def test_rewrite_keeps_a_row_answer_with_a_subquery_beside_its_columns():
    """Positive control: a row answer that reads its own rows still drills."""
    sql = "SELECT id, qty - (SELECT AVG(qty) FROM t) AS d FROM t ORDER BY id LIMIT 5"
    out = rewrite_for_drillthrough(sql, provenance_for=_no_prov)
    assert out.sql == sql


def test_every_engine_aggregate_drills_to_real_row_values_or_is_refused():
    """Sweep: every call DuckDB itself aggregates, over many argument shapes.

    Which calls aggregate is decided by DuckDB (one row back from a four-row
    table), not by the rewrite's own catalog, so a macro or new aggregate the
    rewrite misses fails here. Each rewrite is a DrillthroughError, or its drill
    runs, returns all four rows, and every drilled value is that row's own value
    of a column the aggregate read.
    """
    import duckdb

    from CortexOS.execution.drillthrough import DrillthroughError

    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE t AS SELECT * FROM (VALUES "
        "('a', 1.5, 10.0, 's1', 1, true), ('a', 2.5, 20.0, 's2', 2, false), "
        "('b', 4.0, 30.0, 's3', 3, true), ('b', 8.0, 40.0, 's4', 4, false)"
        ") v(k, x, y, s, i, b)"
    )
    cols = ("x", "y", "s", "i", "b")
    base = {
        r[0]: dict(zip(cols, r[1:], strict=True))
        for r in con.execute("SELECT i, x, y, s, i, b FROM t").fetchall()
    }
    names = sorted(
        r[0]
        for r in con.execute(
            "SELECT DISTINCT function_name FROM duckdb_functions() "
            "WHERE function_type IN ('aggregate', 'macro') AND function_name NOT LIKE '%sleep%'"
        ).fetchall()
    )
    shapes = [
        ("{n}(x)", "x"),
        ("{n}(x, y)", "xy"),
        ("{n}(x, 3)", "x"),
        ("{n}(y, x)", "xy"),
        ("{n}(s)", "s"),
        ("{n}(s, ',')", "s"),
        ("{n}(x, 0.5)", "x"),
        ("{n}(i)", "i"),
        ("{n}(b)", "b"),
        ("{n}(DISTINCT x)", "x"),
        ("{n}(x ORDER BY y DESC)", "xy"),
        ("{n}(x, y, 2)", "xy"),
        ("{n}(i, 1, 4)", "i"),
        ("{n}(x, [0.25, 0.5])", "x"),
        ("{n}(s, x)", "sx"),
        ("{n}(3, x)", "x"),
        ("{n}(1)", ""),
        ("{n}(0.5) WITHIN GROUP (ORDER BY x)", "x"),
        ("{n}() WITHIN GROUP (ORDER BY x)", "x"),
        ("{n}(x IGNORE NULLS)", "x"),
    ]
    failures: list[str] = []
    aggregating = drilled = 0
    for name in names:
        for shape, refs in shapes:
            call = shape.format(n=name)
            try:
                if len(con.execute(f"SELECT {call} AS v FROM t").fetchall()) != 1:
                    continue  # DuckDB does not aggregate this call
            except duckdb.Error:
                continue
            aggregating += 1
            for sql, keyed in (
                (f"SELECT {call} AS v FROM t", False),
                (f"SELECT i, {call} AS v FROM t GROUP BY i", True),
            ):
                try:
                    con.execute(sql).fetchall()
                except duckdb.Error:
                    continue
                try:
                    out = rewrite_for_drillthrough(sql, provenance_for=_no_prov)
                except DrillthroughError:
                    continue
                try:
                    rel = con.execute(out.sql)
                except duckdb.Error as exc:
                    failures.append(f"{sql}: drill fails: {exc}")
                    continue
                names_out = [d[0] for d in rel.description]
                rows = [dict(zip(names_out, r, strict=True)) for r in rel.fetchall()]
                if len(rows) != 4:
                    failures.append(f"{sql}: {len(rows)} rows from {out.sql}")
                    continue
                if "v" in names_out:
                    if keyed:
                        real = [all(r["v"] == base[r["i"]][c] for r in rows) for c in refs]
                    else:
                        got = sorted(repr(r["v"]) for r in rows)
                        real = [got == sorted(repr(base[i][c]) for i in base) for c in refs]
                    if refs and not any(real):
                        failures.append(f"{sql}: values {[r['v'] for r in rows]} from {out.sql}")
                        continue
                drilled += 1
    con.close()
    assert aggregating > 300 and drilled > 400
    assert failures == []


def test_having_drill_serves_only_the_rows_of_the_groups_the_answer_kept():
    """HAVING binds names to aggregate aliases first; WHERE binds them to columns.

    Each rewrite is a DrillthroughError, or its drill returns exactly the rows of
    the groups the answer kept.
    """
    import duckdb

    from CortexOS.execution.drillthrough import DrillthroughError

    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE t AS SELECT * FROM (VALUES "
        "(1, 'a', 5.0), (2, 'a', 50.0), (3, 'b', 40.0), (4, 'b', 45.0), (5, 'c', 1.0)"
        ") AS v(id, k, qty)"
    )
    cases = [
        ("SELECT k, SUM(qty) AS qty FROM t GROUP BY k HAVING qty > 50", False),
        ("SELECT k, MIN(qty) AS qty FROM t GROUP BY k HAVING qty > 30", False),
        ("SELECT k, MAX(qty) AS qty FROM t GROUP BY k HAVING qty < 2 OR k = 'b'", False),
        ("SELECT k, SUM(qty) AS s FROM t GROUP BY k HAVING SUM(qty) > 50", False),
        ("SELECT k, SUM(qty) AS s FROM t GROUP BY k HAVING s > 50", False),
        ("SELECT k, COUNT(*) AS n FROM t GROUP BY ROLLUP (k) HAVING k IS NULL", False),
        ("SELECT k, SUM(qty) AS s FROM t GROUP BY k HAVING k IN (SELECT 'a')", False),
        ("SELECT k, SUM(qty) AS qty FROM t GROUP BY k HAVING k <> 'c'", True),
        ("SELECT k AS g, COUNT(*) AS n FROM t GROUP BY k HAVING g = 'a'", True),
        ("SELECT k, COUNT(*) AS n FROM t GROUP BY k HAVING upper(k) = 'B'", True),
        ("SELECT k, COUNT(*) AS n FROM t GROUP BY 1 HAVING k = 'a'", True),
    ]
    for sql, drills in cases:
        kept = {r[0] for r in con.execute(sql).fetchall()}
        assert kept, sql
        if not drills:
            with pytest.raises(DrillthroughError, match="HAVING"):
                rewrite_for_drillthrough(sql, provenance_for=_no_prov)
            continue
        out = rewrite_for_drillthrough(sql, provenance_for=_no_prov)
        drill_sql = out.sql.replace("SELECT ", "SELECT id, ", 1)
        drilled = sorted(r[0] for r in con.execute(drill_sql).fetchall())
        expected = sorted(
            r[0] for r in con.execute("SELECT id, k FROM t").fetchall() if r[1] in kept
        )
        assert drilled == expected, (sql, out.sql)
        (count,) = con.execute(out.count_sql).fetchone()
        assert count == len(expected), (sql, out.count_sql)
    con.close()
