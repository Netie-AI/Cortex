"""DuckDB warehouse loader + semantic layer helpers."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
# DMS_WAREHOUSE_DB mirrors DMS_OPS_DB (audit ledger): lets a host app isolate
# the warehouse outside the repo. Must be set before this module is imported.
DEFAULT_DB = Path(os.environ.get("DMS_WAREHOUSE_DB") or ROOT / "data" / "dms_demo.duckdb")
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
    import duckdb

    db_path = Path(db_path or DEFAULT_DB)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Rebuilding the warehouse needs the write lock; release any cached reader.
    _evict_read_only(db_path)

    con = duckdb.connect(str(db_path))
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
        rows = [dict(zip(columns, row)) for row in rows_raw]
        return rows, total, all_cols
    finally:
        con.close()


def get_connection(db_path: Path | str | None = None, *, read_only: bool = False):
    """Open the warehouse DuckDB.

    Pass ``read_only=True`` from every query path. DuckDB takes an EXCLUSIVE
    file lock for read-write connections: one read-write connection anywhere
    (a live API process, a benchmark run, a notebook) locks every other process
    out of the file with ``IO Error: ... used by another process``. Read-only
    connections share the file, so N reader processes can serve concurrently.

    A read-only open cannot create a missing file, so an absent DB falls back to
    read-write — the caller that first touches a fresh checkout still works.
    """
    import duckdb

    path = Path(db_path or DEFAULT_DB)
    if read_only and path.exists():
        cursor = _read_only_cursor(path)
        if cursor is not None:
            return cursor
    else:
        # A read-write open must EVICT any cached read-only instance for this
        # file first: DuckDB refuses two connections to one file with different
        # configurations, so a cached reader would otherwise lock the writer out
        # of its own process for good. Readers re-open on their next call.
        _evict_read_only(path)
    return duckdb.connect(str(path))


# One read-only DuckDB instance per process; each caller gets a cursor off it.
# Opening the warehouse file costs ~0.4 s, and the answer path opened a fresh
# connection for EVERY question — roughly half of query latency was connection
# churn. A cursor is an independent execution context over the same instance, so
# concurrent callers stay isolated without paying the open cost again.
#
# Only read-only connections are cached. Caching a read-write one would pin
# DuckDB's exclusive file lock for the life of the process — the very failure
# this flag exists to avoid.
_RO_LOCK = threading.Lock()
_RO_CONNECTIONS: dict[str, Any] = {}


def _read_only_cursor(path: Path):
    # str(path), not path.resolve() — resolve() is a filesystem syscall and this
    # runs on every question. Callers pass DEFAULT_DB or an explicit path, so the
    # string is already stable.
    key = str(path)
    with _RO_LOCK:
        parent = _RO_CONNECTIONS.get(key)
        if parent is None:
            import duckdb

            try:
                parent = duckdb.connect(key, read_only=True)
            except Exception:  # noqa: BLE001
                # A read-write connection is already open in THIS process;
                # DuckDB refuses a second one with a different configuration.
                # Caller falls back to read-write — behaviour as before.
                return None
            _RO_CONNECTIONS[key] = parent
    try:
        return parent.cursor()
    except Exception:  # noqa: BLE001 — instance was closed underneath us
        with _RO_LOCK:
            _RO_CONNECTIONS.pop(key, None)
        return None


def _evict_read_only(path: Path) -> None:
    """Drop the cached read-only instance for one file, if any."""
    key = str(path)
    with _RO_LOCK:
        con = _RO_CONNECTIONS.pop(key, None)
    if con is not None:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def close_cached_connections() -> None:
    """Release the cached read-only instances (tests, and before a reload)."""
    with _RO_LOCK:
        for con in _RO_CONNECTIONS.values():
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
        _RO_CONNECTIONS.clear()


def read_only_queries_enabled() -> bool:
    """Whether query paths inside the serving process should open read-only.

    Default OFF. Turning it ON lets many API/worker processes read the same
    warehouse file concurrently (the single biggest scaling limit of the
    embedded-DuckDB design), but it requires the deployment to keep WRITERS —
    `/dms/add-entry`, CSV reload — in a process that is not also serving reads,
    because DuckDB cannot mix read-only and read-write connections in one
    process. Offline/analysis processes (benchmarks, explain tools) pass
    ``read_only=True`` directly and do not consult this flag.
    """
    return os.environ.get("DMS_READ_ONLY_QUERIES", "").strip().lower() in ("1", "true", "yes", "on")


def main() -> None:
    load_inventory_csv()
    counts = table_row_counts()
    total = sum(counts.values())
    print(f"DuckDB loaded at {DEFAULT_DB}")
    for t, n in counts.items():
        print(f"  {t}: {n}")
    print(f"  TOTAL: {total}")


if __name__ == "__main__":
    main()
