"""DuckDB warehouse loader + semantic layer helpers.

Connection opens live in ``CortexOS.execution.warehouse`` (C4). This module
keeps loaders, previews, and semantic helpers without importing ``duckdb``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from CortexOS.execution.warehouse import (
    DEFAULT_DB,
    close_cached_connections,
    connect_write,
    get_connection,
    read_only_queries_enabled,
    warehouse_path,
)

# Re-export connection helpers so existing callers keep importing from here.
__all__ = [
    "DEFAULT_DB",
    "DEFAULT_SEMANTIC",
    "KNOWN_TABLES",
    "SAMPLES",
    "TABLE_FILES",
    "close_cached_connections",
    "get_connection",
    "load_inventory_csv",
    "load_semantic_layer",
    "preview_table",
    "read_only_queries_enabled",
    "table_row_counts",
    "warehouse_path",
]

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEMANTIC = ROOT / "packs" / "dms" / "semantic_layer.yaml"
SAMPLES = ROOT / "data" / "samples"

TABLE_FILES = {
    "inventory": "inventory_clean.csv",
    "suppliers": "suppliers_clean.csv",
    "locations": "locations_clean.csv",
    "shipments": "shipments_clean.csv",
    "transactions": "transactions_clean.csv",
    "alerts": "alerts_clean.csv",
}

KNOWN_TABLES = tuple(TABLE_FILES.keys())


def load_semantic_layer(path: Path | str | None = None) -> dict[str, Any]:
    path = Path(path or DEFAULT_SEMANTIC)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_table(con, table: str, csv_path: Path) -> None:
    if not csv_path.is_file():
        return
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(
        f"""
        CREATE TABLE {table} AS
        SELECT * FROM read_csv_auto('{csv_path.as_posix()}', header=true)
        """
    )


def _cast_types(con) -> None:
    casts = [
        ("inventory", "quantity_kg", "DOUBLE"),
        ("inventory", "reorder_level_kg", "DOUBLE"),
        ("inventory", "unit_cost_myr", "DOUBLE"),
        ("locations", "capacity_kg", "DOUBLE"),
        ("locations", "current_load_kg", "DOUBLE"),
        ("locations", "latitude", "DOUBLE"),
        ("locations", "longitude", "DOUBLE"),
        ("suppliers", "lead_time_days", "INTEGER"),
        ("suppliers", "risk_score", "DOUBLE"),
        ("shipments", "quantity_kg", "DOUBLE"),
        ("shipments", "cost_myr", "DOUBLE"),
        ("transactions", "quantity_kg", "DOUBLE"),
        ("transactions", "unit_cost_myr", "DOUBLE"),
    ]
    for table, col, typ in casts:
        try:
            con.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE {typ}")
        except Exception:
            pass


def _create_indexes(con) -> None:
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_inv_sku ON inventory(sku)",
        "CREATE INDEX IF NOT EXISTS idx_inv_location ON inventory(location_id)",
        "CREATE INDEX IF NOT EXISTS idx_inv_supplier ON inventory(supplier_id)",
        "CREATE INDEX IF NOT EXISTS idx_ship_status ON shipments(status)",
        "CREATE INDEX IF NOT EXISTS idx_ship_dest ON shipments(destination_location_id)",
        "CREATE INDEX IF NOT EXISTS idx_txn_sku ON transactions(sku)",
        "CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity)",
        "CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved)",
    ]
    for ddl in indexes:
        try:
            con.execute(ddl)
        except Exception:
            pass


def load_inventory_csv(
    csv_path: Path | str | None = None,
    db_path: Path | str | None = None,
) -> Path:
    db_path = warehouse_path(db_path)
    con = connect_write(db_path)
    try:
        for table, fname in TABLE_FILES.items():
            path = SAMPLES / fname
            if table == "inventory" and csv_path is not None:
                path = Path(csv_path)
            _load_table(con, table, path)
        _cast_types(con)
        _create_indexes(con)
    finally:
        con.close()
    return db_path


def table_row_counts(db_path: Path | str | None = None) -> dict[str, int]:
    # Pure read: must not take the write lock, and must not evict a cached
    # reader that other callers in this process are still using.
    con = get_connection(db_path, read_only=read_only_queries_enabled())
    counts: dict[str, int] = {}
    try:
        for table in KNOWN_TABLES:
            try:
                row = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                counts[table] = int(row[0]) if row else 0
            except Exception:
                counts[table] = 0
    finally:
        con.close()
    return counts


def preview_table(
    table: str,
    *,
    from_row: int = 0,
    to_row: int = 100,
    cols: list[str] | None = None,
    db_path: Path | str | None = None,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    if table not in KNOWN_TABLES:
        raise ValueError(f"Unknown table: {table}")

    limit = min(max(to_row - from_row, 1), 500)
    con = get_connection(db_path, read_only=read_only_queries_enabled())
    try:
        total = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        col_rows = con.execute(
            f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}' ORDER BY ordinal_position"
        ).fetchall()
        all_cols = [r[0] for r in col_rows]
        use_cols = [c for c in (cols or all_cols) if c in all_cols] or all_cols
        col_sql = ", ".join(use_cols)
        rel = con.execute(
            f"SELECT {col_sql} FROM {table} LIMIT {limit} OFFSET {from_row}"
        )
        rows_raw = rel.fetchall()
        columns = [d[0] for d in rel.description] if rel.description else use_cols
        rows = [dict(zip(columns, row, strict=False)) for row in rows_raw]
        return rows, total, all_cols
    finally:
        con.close()


def main() -> None:
    load_inventory_csv()
    counts = table_row_counts()
    total = sum(counts.values())
    print(f"DuckDB loaded at {warehouse_path()}")
    for t, n in counts.items():
        print(f"  {t}: {n}")
    print(f"  TOTAL: {total}")


if __name__ == "__main__":
    main()
