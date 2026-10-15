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
