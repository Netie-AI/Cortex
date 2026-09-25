"""CortexOS.execution.schema_check — columns resolved against the real warehouse.

Ground truth is DuckDB's own binder: every case is also EXPLAINed against the
same file, and the check must agree with it (no false refusal, no false pass).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from CortexOS.execution.schema_check import (
    UNKNOWN_COLUMN,
    UNKNOWN_TABLE,
    check_columns,
    check_generated_sql,
    warehouse_schema,
)
from CortexOS.execution.warehouse import close_cached_connections


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE transactions (txn_id VARCHAR, sku VARCHAR, location_id VARCHAR, "
        "txn_type VARCHAR, quantity_kg DOUBLE, ts TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE locations (location_id VARCHAR, name VARCHAR, capacity_kg DOUBLE, "
        "current_load_kg DOUBLE)"
    )
    con.execute("CREATE TABLE shipments (shipment_id VARCHAR, sku VARCHAR, status VARCHAR)")
    con.execute(
        "CREATE TABLE inventory (sku VARCHAR, location_id VARCHAR, supplier_id VARCHAR, "
        "quantity_kg DOUBLE)"
    )
    con.close()
    close_cached_connections()
    yield path
    close_cached_connections()


REFUSED = [
    ("SELECT timestamp FROM transactions", f"{UNKNOWN_COLUMN}:transactions.timestamp"),
    (
        "SELECT date_trunc('month', timestamp) AS m, SUM(quantity_kg) FROM transactions GROUP BY 1",
        f"{UNKNOWN_COLUMN}:transactions.timestamp",
    ),
    (
        "SELECT l.location_name, SUM(i.quantity_kg) FROM inventory i "
        "JOIN locations l ON i.location_id = l.location_id GROUP BY 1",
        f"{UNKNOWN_COLUMN}:locations.location_name",
    ),
    ("SELECT supplier_id, COUNT(*) FROM shipments GROUP BY 1", f"{UNKNOWN_COLUMN}:shipments.supplier_id"),
    (
        "SELECT s.supplier_id FROM shipments s JOIN inventory i ON s.sku = i.sku",
        f"{UNKNOWN_COLUMN}:shipments.supplier_id",
    ),
    (
        "SELECT sku FROM inventory i WHERE EXISTS "
        "(SELECT 1 FROM shipments s WHERE s.sku = i.sku AND s.carrier = 'x')",
        f"{UNKNOWN_COLUMN}:shipments.carrier",
    ),
    ("SELECT sku FROM suppliers", f"{UNKNOWN_TABLE}:suppliers"),
]

ALLOWED = [
    "SELECT t.sku, SUM(t.quantity_kg) AS q FROM transactions t GROUP BY t.sku ORDER BY q DESC LIMIT 3",
    "SELECT supplier_id FROM shipments s JOIN inventory i ON s.sku = i.sku",
    "WITH x AS (SELECT sku, SUM(quantity_kg) AS q FROM transactions GROUP BY sku) SELECT sku, q FROM x",
    "SELECT sku FROM (SELECT *, CAST(ts AS DATE) AS day FROM transactions) f WHERE day > CURRENT_DATE",
    "SELECT name, current_load_kg / capacity_kg * 100 AS util FROM locations ORDER BY util DESC",
    "SELECT sku FROM inventory WHERE sku IN (SELECT sku FROM shipments WHERE status = 'delayed')",
    "SELECT location_id FROM inventory JOIN locations USING (location_id)",
    "SELECT sku, SUM(quantity_kg) total FROM inventory GROUP BY sku HAVING total > 5 ORDER BY total",
    "SELECT * FROM inventory",
    "SELECT COUNT(*) FROM transactions",
]


def _binds(path: Path, sql: str) -> bool:
    con = duckdb.connect(str(path), read_only=True)
    try:
        con.execute("EXPLAIN " + sql)
        return True
    except duckdb.Error:
        return False
    finally:
        con.close()


def test_schema_is_read_from_the_file(db: Path) -> None:
    schema = warehouse_schema(db)
    assert schema is not None
    assert "ts" in schema["transactions"]
    assert "timestamp" not in schema["transactions"]
    assert "suppliers" not in schema


@pytest.mark.parametrize(("sql", "violation"), REFUSED)
def test_missing_columns_are_refused_and_named(db: Path, sql: str, violation: str) -> None:
    schema = warehouse_schema(db)
    result = check_columns(sql, schema)
    close_cached_connections()
    assert result.checked is True
    assert result.ok is False
    assert violation in result.violations
    assert _binds(db, sql) is False  # DuckDB's binder agrees


@pytest.mark.parametrize("sql", ALLOWED)
def test_bindable_sql_passes(db: Path, sql: str) -> None:
    result = check_generated_sql(sql, db)
    close_cached_connections()
    assert result.checked is True
    assert result.ok is True, result.violations
    assert _binds(db, sql) is True


def test_no_warehouse_is_unchecked_not_passed(tmp_path: Path) -> None:
    result = check_generated_sql("SELECT timestamp FROM transactions", tmp_path / "nope.duckdb")
    assert result.checked is False
    assert "no executing warehouse" in result.detail


def test_a_table_function_source_is_not_proven_bindable(db: Path, tmp_path: Path) -> None:
    """Columns of a non-warehouse source cannot be resolved from the catalogue: refuse."""
    target = tmp_path / "secret.csv"
    target.write_text("a\n1\n", encoding="utf-8")
    result = check_generated_sql(f"SELECT a FROM read_csv('{target.as_posix()}')", db)
    assert result.ok is False
