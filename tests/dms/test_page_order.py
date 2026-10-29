"""A LIMIT that drops rows orders by the table key, not by question text.

DuckDB does not stable-sort. ``apply_page_tiebreak`` appends ``row_key``
(or the GROUP BY grain) when the result is larger than the LIMIT. A result
that already fits is left byte-identical. Generated SQL and governed SQL
share this function.
"""

from __future__ import annotations

from CortexOS.dms.sql_guardrail import apply_page_tiebreak, validate_sql
from CortexOS.dms.warehouse_db import load_semantic_layer

SEM = load_semantic_layer()


def _safe(sql: str) -> str:
    result = validate_sql(sql, SEM)
    assert result.passed and result.safe_sql
    return result.safe_sql


def test_cut_page_appends_the_inventory_lot_key() -> None:
    sql = _safe(
        "SELECT i.sku, i.quantity_kg FROM inventory i "
        "LEFT JOIN locations l ON i.location_id = l.location_id "
        "ORDER BY i.quantity_kg ASC"
    )
    tied = apply_page_tiebreak(sql, SEM, 1218)
    assert tied.endswith(
        "ORDER BY i.quantity_kg ASC, i.sku ASC, i.location_id ASC, "
        "i.storage_bin ASC, l.location_id ASC LIMIT 1000"
    )


def test_a_result_that_fits_keeps_its_sql_bytes() -> None:
    sql = _safe(
        "SELECT i.sku, i.quantity_kg FROM inventory i "
        "LEFT JOIN locations l ON i.location_id = l.location_id "
        "WHERE l.location_code = 'WH-A' ORDER BY i.quantity_kg ASC"
    )
    assert apply_page_tiebreak(sql, SEM, 8) == sql


def test_same_column_on_the_joined_table_does_not_cover_the_lot_key() -> None:
    sql = _safe(
        "SELECT i.sku, i.quantity_kg FROM inventory i "
        "LEFT JOIN locations l ON i.location_id = l.location_id "
        "ORDER BY l.location_id ASC"
    )
    tied = apply_page_tiebreak(sql, SEM, 1218)
    assert tied.endswith(
        "ORDER BY l.location_id ASC, i.sku ASC, i.location_id ASC, i.storage_bin ASC LIMIT 1000"
    )


def test_an_order_that_already_names_the_key_is_unchanged_when_cut() -> None:
    sql = _safe("SELECT shipment_id FROM shipments ORDER BY shipment_id")
    assert apply_page_tiebreak(sql, SEM, 5000) == sql


def test_grouped_cut_breaks_ties_on_the_group_key_only() -> None:
    sql = _safe(
        "SELECT category, COUNT(*) AS sku_count FROM inventory "
        "GROUP BY category ORDER BY sku_count DESC"
    )
    tied = apply_page_tiebreak(sql, SEM, 5000)
    assert tied.endswith("ORDER BY sku_count DESC, category ASC LIMIT 1000")
    assert "storage_bin" not in tied


def test_unknown_count_fails_closed_onto_the_key() -> None:
    sql = _safe("SELECT supplier_id FROM suppliers ORDER BY risk_score DESC LIMIT 10")
    tied = apply_page_tiebreak(sql, SEM, None)
    assert "suppliers.supplier_id ASC" in tied


def test_no_order_by_stays_unordered() -> None:
    sql = _safe("SELECT COUNT(*) AS n FROM inventory")
    assert "ORDER BY" not in sql
    assert apply_page_tiebreak(sql, SEM, 1) == sql
