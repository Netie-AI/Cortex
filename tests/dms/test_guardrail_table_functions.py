"""GUARDRAIL-TABLEFN-03: a function read as a relation is refused by name.

``validate_sql`` checks relations by name against the grant. A table function
has no name to check: ``read_csv(...)`` and ``duckdb_tables()`` parse to a table
with an empty name, and ``LATERAL read_csv(...)`` parses to no table at all, so
``SELECT r.column0 AS column0 FROM inventory, LATERAL read_csv(<file>) r``
served the file. The gate now refuses any function in FROM, a JOIN, a comma
join or LATERAL, including inside subqueries and CTE bodies, with
``TABLE_FUNCTION:<name>``. The one exception is the generator the gate already
served: ``UNNEST`` over literal arguments.

Every must-fail case is run on DuckDB first, so each one is a query DuckDB
answers. Positive controls show ordinary granted queries serve exactly the
rows DuckDB returns, as before. Alias and column binding are not changed.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from CortexOS.dms.sql_guardrail import guard_and_execute, validate_sql

SECRET = "TOPSECRET-7f3a"
SEMANTIC = {
    "tables": {
        "inventory": {"columns": ["sku", "quantity_kg", "location_id"]},
        "locations": {"columns": ["location_id", "location_name"]},
    }
}


@pytest.fixture
def secret_file(tmp_path: Path) -> Path:
    path = tmp_path / "secret.csv"
    path.write_text(f"column0\n{SECRET}\n", encoding="utf-8")
    return path


@pytest.fixture
def con(secret_file: Path) -> duckdb.DuckDBPyConnection:
    db = duckdb.connect()
    db.execute(
        "CREATE TABLE inventory(sku VARCHAR, quantity_kg DOUBLE, location_id VARCHAR);"
        "INSERT INTO inventory VALUES ('SKU-A', 5, 'L1'), ('SKU-B', 12, 'L1'),"
        " ('SKU-C', 30, 'L2');"
        "CREATE TABLE locations(location_id VARCHAR, location_name VARCHAR);"
        "INSERT INTO locations VALUES ('L1', 'North'), ('L2', 'South');"
        # Not in the grant. A relation function must not reach it either.
        f"CREATE TABLE secrets(column0 VARCHAR); INSERT INTO secrets VALUES ('{SECRET}');"
    )
    yield db
    db.close()


# A function read as a relation, in every relation position. ``{p}`` is a file
# outside the grant holding SECRET; ``{d}`` is its directory.
MUST_FAIL = {
    # FROM, comma join, JOIN
    "from_reader": "SELECT * FROM read_csv('{p}')",
    "from_schema_qualified_reader": "SELECT * FROM main.read_csv('{p}')",
    "comma_join_reader": "SELECT i.sku, r.column0 AS column0 FROM inventory i, read_csv('{p}') r",
    "join_reader": (
        "SELECT i.sku FROM inventory i JOIN read_csv('{p}') r ON r.column0 LIKE 'TOPSECRET%'"
    ),
    "left_join_reader": (
        "SELECT i.sku, r.column0 AS column0 FROM inventory i LEFT JOIN read_csv('{p}') r ON true"
    ),
    "positional_join_reader": (
        "SELECT r.column0 AS column0 FROM inventory POSITIONAL JOIN read_csv('{p}') r"
    ),
    "parenthesised_join_reader": "SELECT sku FROM (inventory CROSS JOIN read_csv('{p}'))",
    "read_csv_auto": "SELECT r.column0 AS column0 FROM inventory, read_csv_auto('{p}') r",
    "read_text": "SELECT r.content AS content FROM inventory, read_text('{p}') r",
    "glob": "SELECT g.file AS file FROM inventory, glob('{d}/*') g",
    # LATERAL: no exp.Table at all, so the base gate served these outright.
    "lateral_reader": "SELECT r.column0 AS column0 FROM inventory, LATERAL read_csv('{p}') r",
    "cross_join_lateral_reader": (
        "SELECT count(*) AS n FROM inventory CROSS JOIN LATERAL read_csv('{p}') r"
    ),
    "left_join_lateral_reader": (
        "SELECT r.column0 AS column0 FROM inventory LEFT JOIN LATERAL read_csv('{p}') r ON true"
    ),
    "lateral_glob": "SELECT g.file AS file FROM inventory, LATERAL glob('{d}/*') g",
    # Nested: subqueries, derived tables, CTE bodies.
    "where_in_subquery": "SELECT sku FROM inventory WHERE sku IN (SELECT column0 FROM read_csv('{p}'))",
    "where_exists_subquery": (
        "SELECT sku FROM inventory WHERE EXISTS (SELECT 1 FROM read_csv('{p}') r "
        "WHERE r.column0 LIKE 'TOPSECRET%')"
    ),
    "scalar_subquery_in_select": (
        "SELECT (SELECT max(column0) FROM read_csv('{p}')) AS column0 FROM inventory"
    ),
    "derived_table": "SELECT d.column0 AS column0 FROM (SELECT * FROM read_csv('{p}')) d",
    "lateral_subquery_holding_reader": (
        "SELECT x.column0 AS column0 FROM inventory i, LATERAL (SELECT * FROM read_csv('{p}')) x"
    ),
    "cte_reader": "WITH c AS (SELECT * FROM read_csv('{p}')) SELECT i.sku FROM inventory i, c",
    "cte_reader_read_by_alias": (
        "WITH c AS (SELECT column0 FROM read_csv('{p}')) "
        "SELECT c.column0 AS column0 FROM inventory i JOIN c ON true"
    ),
    "cte_inside_subquery": (
        "SELECT sku FROM inventory WHERE sku IN "
        "(WITH c AS (SELECT * FROM read_csv('{p}')) SELECT column0 FROM c)"
    ),
    # Catalog readers and dynamic-SQL readers next to a granted table.
    "duckdb_tables": "SELECT t.table_name AS table_name FROM inventory, duckdb_tables() t",
    "duckdb_columns": "SELECT c.column_name AS column_name FROM inventory, duckdb_columns() c",
    "duckdb_settings_in_cte": (
        "WITH s AS (SELECT * FROM duckdb_settings()) SELECT i.sku FROM inventory i, s"
    ),
    "query_table": "SELECT q.column0 AS column0 FROM inventory, query_table('secrets') q",
    "query": "SELECT q.column0 AS column0 FROM inventory, query('SELECT column0 FROM secrets') q",
    "lateral_query_table": (
        "SELECT q.column0 AS column0 FROM inventory, LATERAL query_table('secrets') q"
    ),
}


def _sql(template: str, secret_file: Path) -> str:
    return template.format(p=secret_file.as_posix(), d=secret_file.parent.as_posix())


@pytest.mark.parametrize("template", list(MUST_FAIL.values()), ids=list(MUST_FAIL))
def test_must_fail_function_as_relation_is_refused_by_name(
    template: str, secret_file: Path, con: duckdb.DuckDBPyConnection
) -> None:
    sql = _sql(template, secret_file)
    con.execute(sql).fetchall()  # DuckDB answers it: the case is real

    result = validate_sql(sql, SEMANTIC)
    assert result.passed is False
    assert result.safe_sql is None
    assert result.violations, sql
    assert all(v.startswith("TABLE_FUNCTION:") for v in result.violations), result.violations


@pytest.mark.parametrize(
    "name",
    ["lateral_reader", "comma_join_reader", "cte_reader_read_by_alias", "query_table"],
)
def test_must_fail_reader_serves_no_rows(
    name: str, secret_file: Path, con: duckdb.DuckDBPyConnection
) -> None:
    """The rows the customer gets back, not only the verdict."""
    sql = _sql(MUST_FAIL[name], secret_file)
    assert SECRET in json.dumps(con.execute(sql).fetchall())  # DuckDB leaks it

    result, rows, entry = guard_and_execute(sql, SEMANTIC, con)
    assert result.passed is False
    assert rows == []
    assert entry.safe_sql is None
    assert entry.row_count is None
    assert SECRET not in json.dumps({"rows": rows, "violations": result.violations})


def test_the_violation_names_the_function(secret_file: Path) -> None:
    sql = _sql(MUST_FAIL["lateral_reader"], secret_file)
    assert validate_sql(sql, SEMANTIC).violations == ["TABLE_FUNCTION:read_csv"]
    sql = _sql(MUST_FAIL["duckdb_tables"], secret_file)
    assert validate_sql(sql, SEMANTIC).violations == ["TABLE_FUNCTION:duckdb_tables"]
    sql = _sql(MUST_FAIL["query_table"], secret_file)
    assert validate_sql(sql, SEMANTIC).violations == ["TABLE_FUNCTION:query_table"]


# Functions as relations that are not a literal-only UNNEST. Base refused the
# FROM forms only by accident (an empty table name) and served the UNNEST and
# LATERAL forms; all now fail closed by name.
FAIL_CLOSED = {
    "range_relation": ("SELECT sku FROM inventory, range(3)", "TABLE_FUNCTION:generate_series"),
    "generate_series_relation": (
        "SELECT sku FROM inventory, generate_series(1, 3) g(n)",
        "TABLE_FUNCTION:generate_series",
    ),
    "lateral_range": (
        "SELECT sku FROM inventory, LATERAL range(3) g",
        "TABLE_FUNCTION:generate_series",
    ),
    "unnest_over_a_column": (
        "SELECT i.sku FROM inventory i, unnest(i.sku) u",
        "TABLE_FUNCTION:unnest",
    ),
    "unnest_over_a_call": (
        "SELECT sku FROM inventory, unnest(generate_series(1, 3)) u",
        "TABLE_FUNCTION:unnest",
    ),
    "unnest_over_a_subquery": (
        "SELECT sku FROM inventory, unnest((SELECT list(sku) FROM inventory)) u",
        "TABLE_FUNCTION:unnest",
    ),
    "lateral_unnest_over_a_column": (
        "SELECT i.sku FROM inventory i, LATERAL unnest(i.sku) u",
        "TABLE_FUNCTION:unnest",
    ),
    "table_wrapper": ("SELECT sku FROM inventory, TABLE(range(3))", "TABLE_FUNCTION:table"),
    "granted_name_called": ("SELECT sku FROM inventory()", "TABLE_FUNCTION:inventory"),
}


@pytest.mark.parametrize(("sql", "needle"), list(FAIL_CLOSED.values()), ids=list(FAIL_CLOSED))
def test_function_relation_beyond_a_literal_generator_fails_closed(sql: str, needle: str) -> None:
    result = validate_sql(sql, SEMANTIC)
    assert result.passed is False
    assert result.safe_sql is None
    assert needle in result.violations


# Ordinary granted queries. Each one passed the base gate; each must still pass
# and serve exactly the rows DuckDB returns for the caller's SQL.
POSITIVE = {
    "plain": "SELECT sku, quantity_kg FROM inventory ORDER BY sku",
    "where": "SELECT sku FROM inventory WHERE quantity_kg > 6 ORDER BY sku",
    "join": (
        "SELECT i.sku, l.location_name FROM inventory i "
        "JOIN locations l ON i.location_id = l.location_id ORDER BY i.sku"
    ),
    "left_join": (
        "SELECT i.sku, l.location_name FROM inventory i "
        "LEFT JOIN locations l ON i.location_id = l.location_id ORDER BY i.sku"
    ),
    "comma_join": (
        "SELECT i.sku, l.location_name FROM inventory i, locations l "
        "WHERE i.location_id = l.location_id ORDER BY i.sku"
    ),
    "scalar_subquery": (
        "SELECT sku FROM inventory WHERE quantity_kg > (SELECT avg(quantity_kg) FROM inventory) "
        "ORDER BY sku"
    ),
    "in_subquery": (
        "SELECT sku FROM inventory WHERE location_id IN "
        "(SELECT location_id FROM locations WHERE location_name = 'North') ORDER BY sku"
    ),
    "exists_subquery": (
        "SELECT i.sku FROM inventory i WHERE EXISTS (SELECT 1 FROM locations l "
        "WHERE l.location_id = i.location_id AND l.location_name = 'South') ORDER BY i.sku"
    ),
    "derived_table": (
        "SELECT d.sku FROM (SELECT sku, quantity_kg FROM inventory WHERE quantity_kg > 6) d "
        "ORDER BY d.sku"
    ),
    # The base gate checks a CTE's name as a table, so the CTE it serves is one
    # named like a granted table. That is unchanged.
    "cte_named_like_granted_table": (
        "WITH locations AS (SELECT location_id, location_name FROM locations "
        "WHERE location_name = 'North') SELECT i.sku, l.location_name FROM inventory i "
        "JOIN locations l ON i.location_id = l.location_id ORDER BY i.sku"
    ),
    "group_by_alias": (
        "SELECT location_id AS loc, sum(quantity_kg) AS total FROM inventory "
        "GROUP BY loc ORDER BY loc"
    ),
    "order_by_alias": (
        "SELECT location_id, count(*) AS n FROM inventory GROUP BY location_id ORDER BY n DESC"
    ),
    "having_alias": (
        "SELECT location_id, sum(quantity_kg) AS total FROM inventory "
        "GROUP BY location_id HAVING total > 20 ORDER BY location_id"
    ),
    "qualify_alias": (
        "SELECT sku, location_id, row_number() OVER "
        "(PARTITION BY location_id ORDER BY quantity_kg DESC) AS rn FROM inventory "
        "QUALIFY rn = 1 ORDER BY sku"
    ),
    "window": (
        "SELECT sku, sum(quantity_kg) OVER (PARTITION BY location_id) AS total "
        "FROM inventory ORDER BY sku"
    ),
    # The generator this gate already served stays served.
    "unnest_literal_generator": (
        "SELECT i.sku, u.x AS x FROM inventory i, unnest([1, 2]) u(x) ORDER BY i.sku, x"
    ),
    "lateral_unnest_literal_generator": (
        "SELECT i.sku, u.x AS x FROM inventory i, LATERAL unnest(['a', 'b']) u(x) ORDER BY i.sku, x"
    ),
    "join_unnest_literal_generator": (
        "SELECT i.sku, u.x AS x FROM inventory i JOIN unnest([5, 12]) u(x) "
        "ON i.quantity_kg = u.x ORDER BY i.sku"
    ),
    # UNNEST as a scalar in the select list is not a relation.
    "scalar_unnest": "SELECT sku, unnest([1, 2]) AS x FROM inventory ORDER BY sku, x",
    # VALUES is a relation but not a function.
    "values_relation": "SELECT i.sku FROM inventory i, (VALUES (1)) v(x) ORDER BY i.sku",
}


@pytest.mark.parametrize("sql", list(POSITIVE.values()), ids=list(POSITIVE))
def test_positive_control_ordinary_query_serves_duckdb_rows(
    sql: str, con: duckdb.DuckDBPyConnection
) -> None:
    expected = con.execute(sql).fetchall()
    assert expected  # a control that returns nothing proves nothing

    result, rows, _ = guard_and_execute(sql, SEMANTIC, con)
    assert result.passed is True, result.violations
    assert result.safe_sql
    assert [tuple(r.values()) for r in rows] == expected


def test_cte_refusal_is_unchanged_and_not_a_table_function() -> None:
    """A CTE under its own name was refused as an unknown table before; still so."""
    sql = (
        "WITH heavy AS (SELECT sku, location_id FROM inventory WHERE quantity_kg > 6) "
        "SELECT h.sku FROM heavy h ORDER BY h.sku"
    )
    assert validate_sql(sql, SEMANTIC).violations == ["UNKNOWN_TABLE:heavy"]


# -- the customer response: POST /dms/lakehouse/query takes the caller's SQL --


@pytest.fixture
def lake_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from packs.dms.lakehouse import catalog
    from packs.dms.lakehouse import tables as lt

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_LAKEHOUSE_HOME", str(tmp_path / "lakehouse"))
    monkeypatch.setenv("DMS_LAKEHOUSE_FORCE_FALLBACK", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "dms_demo.duckdb"))
    catalog.reset_mode_cache()
    lt.write_table(
        "silver", "stock", rows=[{"sku": "SKU-A", "qty": 5.0}, {"sku": "SKU-B", "qty": 7.0}]
    )
    from CortexOS.api.app import create_app

    yield TestClient(create_app(), headers={"X-API-Key": "dms-demo-admin-key"})
    catalog.reset_mode_cache()


def test_must_fail_lakehouse_query_lateral_reader_returns_no_file_content(
    lake_client, secret_file: Path
) -> None:
    sql = (
        "SELECT r.column0 AS column0 FROM lake.silver.stock, "
        f"LATERAL read_csv('{secret_file.as_posix()}') r"
    )
    res = lake_client.post("/dms/lakehouse/query", json={"sql": sql})
    assert res.status_code == 400, res.text
    body = res.json()
    assert body["detail"]["violations"] == ["TABLE_FUNCTION:read_csv"]
    assert "rows" not in body
    assert SECRET not in res.text


def test_positive_control_lakehouse_query_serves_the_table_rows(lake_client) -> None:
    res = lake_client.post(
        "/dms/lakehouse/query",
        json={"sql": "SELECT sku, qty FROM lake.silver.stock ORDER BY sku"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["rows"] == [{"sku": "SKU-A", "qty": 5.0}, {"sku": "SKU-B", "qty": 7.0}]
    assert body["row_count"] == 2
