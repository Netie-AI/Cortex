"""AI-SQL-READS-TABLE-01: output lineage read from parsed SQL (sqlglot), not text.

Each output column must derive from a column of a granted base table, or be a
row count / aggregate over one. Positive controls are ordinary analytics.
"""

from __future__ import annotations

import pytest

from CortexOS.dms.sql_lineage import (
    OUTPUT_LINEAGE_UNREADABLE,
    lineage_violations,
    underived_outputs,
)

GRANTED = {"transactions", "inventory"}

DERIVED = [
    "SELECT txn_type, COUNT(*) AS n FROM transactions GROUP BY txn_type ORDER BY n DESC",
    "SELECT COUNT(*) FROM transactions",
    "SELECT COUNT(1) AS n FROM transactions",
    "SELECT SUM(quantity_kg) AS kg FROM transactions",
    "SELECT COUNT(DISTINCT sku) AS c FROM inventory",
    "SELECT ROUND(AVG(quantity_kg), 2) AS a FROM inventory",
    "SELECT txn_type, SUM(quantity_kg) * 2 + 1 AS x FROM transactions GROUP BY 1",
    "SELECT CASE WHEN quantity_kg > 100 THEN 'big' ELSE 'small' END AS size FROM transactions",
    "WITH t AS (SELECT sku, SUM(quantity_kg) AS kg FROM transactions GROUP BY sku) "
    "SELECT sku, kg FROM t ORDER BY kg DESC",
    "WITH t AS (SELECT * FROM transactions) SELECT t.sku, COUNT(*) AS n FROM t GROUP BY 1",
    "WITH a AS (SELECT sku FROM inventory), b AS (SELECT sku FROM a) SELECT sku FROM b",
    "SELECT i.sku, t.txn_type FROM inventory i JOIN transactions t ON t.sku = i.sku",
    "SELECT * FROM transactions",
    "SELECT t.* FROM transactions t",
    "SELECT s.n FROM (SELECT COUNT(*) AS n FROM transactions) s",
    "SELECT sku FROM inventory UNION ALL SELECT sku FROM transactions",
    "SELECT sku FROM inventory EXCEPT SELECT 'X'",
    "SELECT (SELECT MAX(quantity_kg) FROM transactions t WHERE t.sku = i.sku) AS mx FROM inventory i",
    "SELECT sku, ROW_NUMBER() OVER (ORDER BY quantity_kg) AS rk FROM inventory",
    "SELECT quantity_kg AS q, q * 2 AS dbl FROM inventory",
    "SELECT DATE_TRUNC('month', CAST(ts AS DATE)) AS m, COUNT(*) AS n FROM transactions GROUP BY 1",
    'SELECT "SKU" FROM "Inventory"',
]

UNDERIVED = [
    ("SELECT 'IN' AS txn_type, 424242 AS n FROM transactions", ["txn_type", "n"]),
    ("SELECT 424242 AS n FROM transactions LIMIT 1", ["n"]),
    ("SELECT 424242 AS n FROM (VALUES (1))", ["n"]),
    ("SELECT 424242 AS n", ["n"]),
    ("SELECT sku, 424242 AS n FROM inventory", ["n"]),
    ("SELECT 424242 AS n FROM inventory WHERE sku IS NOT NULL", ["n"]),
    ("SELECT COUNT(*) AS n FROM (VALUES (1), (2))", ["n"]),
    ("SELECT MAX(424242) AS n FROM inventory", ["n"]),
    ("SELECT (SELECT 424242) AS n FROM inventory", ["n"]),
    ("SELECT n FROM (SELECT 424242 AS n FROM inventory) s", ["n"]),
    ("WITH c AS (SELECT 424242 AS n) SELECT n FROM c, inventory", ["n"]),
    ("SELECT n FROM inventory, (SELECT 424242 AS n) s", ["n"]),
    ("SELECT sku FROM inventory UNION ALL SELECT 'FAKE'", ["sku"]),
    (
        "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r WHERE n < 5) "
        "SELECT n FROM r",
        ["n"],
    ),
    ("SELECT * FROM (VALUES (424242)) v", ["*"]),
    ("SELECT * FROM inventory, (VALUES (424242)) v", ["*"]),
    ("SELECT (SELECT n FROM inventory LIMIT 1) AS z FROM (SELECT 424242 AS n) o", ["z"]),
    ("SELECT CURRENT_DATE AS d FROM inventory", ["d"]),
    ("SELECT 424242 AS a, a AS b FROM inventory", ["a", "b"]),
    ("SELECT x FROM ungranted", ["x"]),
    ("SELECT COUNT(*) AS n FROM ungranted", ["n"]),
]


@pytest.mark.parametrize("sql", DERIVED)
def test_table_derived_outputs_pass(sql: str) -> None:
    assert underived_outputs(sql, GRANTED) == []
    assert lineage_violations(sql, GRANTED) == []


@pytest.mark.parametrize(("sql", "bad"), UNDERIVED)
def test_outputs_not_read_from_a_granted_table_are_named(sql: str, bad: list[str]) -> None:
    assert underived_outputs(sql, GRANTED) == bad
    assert lineage_violations(sql, GRANTED) == [f"OUTPUT_NOT_FROM_TABLE:{name}" for name in bad]


@pytest.mark.parametrize("sql", ["SELECT FROM WHERE (", "", "UPDATE inventory SET sku = 1"])
def test_unreadable_sql_fails_closed(sql: str) -> None:
    assert lineage_violations(sql, GRANTED) == [OUTPUT_LINEAGE_UNREADABLE]


def test_grant_names_fold_as_duckdb_folds() -> None:
    assert underived_outputs("SELECT sku FROM INVENTORY", {"inventory"}) == []
    assert underived_outputs("SELECT sku FROM inventory", {"Inventory"}) == []
    assert underived_outputs("SELECT sku FROM inventory", {"transactions"}) == ["sku"]
