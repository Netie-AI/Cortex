"""L0 seed migration — land the demo warehouse data into the lakehouse.

bronze.<t>_raw  ← *_messy.csv, all-VARCHAR permissive + _ingest_meta
silver.<t>      ← *_clean.csv, typed (same casts as warehouse_db._cast_types)
gold.*          ← the 3 ranked-query shapes materialized

Idempotent: re-running replaces tables. Leaves data/dms_demo.duckdb untouched
(query_service still reads that until Q2 switches to lakehouse views).

Run: python -m scripts.lakehouse_migrate
"""
from __future__ import annotations

from pathlib import Path

from packs.dms.lakehouse.catalog import connect, lakehouse_mode
from packs.dms.lakehouse import tables as lt

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"

TABLES = ("inventory", "suppliers", "locations", "shipments", "transactions", "alerts")

# Same numeric casts warehouse_db._cast_types applies, expressed as silver SELECTs.
_SILVER_CASTS: dict[str, dict[str, str]] = {
    "inventory": {"quantity_kg": "DOUBLE", "reorder_level_kg": "DOUBLE", "unit_cost_myr": "DOUBLE"},
    "locations": {"capacity_kg": "DOUBLE", "current_load_kg": "DOUBLE",
                  "latitude": "DOUBLE", "longitude": "DOUBLE"},
    "suppliers": {"lead_time_days": "INTEGER", "risk_score": "DOUBLE"},
    "shipments": {"quantity_kg": "DOUBLE", "cost_myr": "DOUBLE"},
    "transactions": {"quantity_kg": "DOUBLE", "unit_cost_myr": "DOUBLE"},
    "alerts": {},
}


def _csv(name: str, messy: bool) -> Path:
    return SAMPLES / f"{name}_{'messy' if messy else 'clean'}.csv"


def _column_names(con, path: Path) -> list[str]:
    rel = con.execute(
        f"SELECT * FROM read_csv_auto('{path.as_posix()}', header=true) LIMIT 0")
    return [d[0] for d in rel.description]


def migrate_bronze(con) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in TABLES:
        path = _csv(t, messy=True)
        if not path.is_file():
            path = _csv(t, messy=False)
        if not path.is_file():
            continue
        target = f"lake.bronze.{t}_raw"
        con.execute(f"DROP TABLE IF EXISTS {target}")
        con.execute(
            f"CREATE TABLE {target} AS SELECT *, "
            f"  '{path.name}' AS _source_file, "
            f"  now()::VARCHAR AS _loaded_at "
            f"FROM read_csv_auto('{path.as_posix()}', header=true, "
            f"                    all_varchar=true, ignore_errors=true)"
        )
        counts[t] = con.execute(f"SELECT COUNT(*) FROM {target}").fetchone()[0]
    return counts


def migrate_silver(con) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in TABLES:
        path = _csv(t, messy=False)
        if not path.is_file():
            continue
        cols = _column_names(con, path)
        casts = _SILVER_CASTS.get(t, {})
        projected = ", ".join(
            f"TRY_CAST({c} AS {casts[c]}) AS {c}" if c in casts else c for c in cols)
        target = f"lake.silver.{t}"
        con.execute(f"DROP TABLE IF EXISTS {target}")
        con.execute(
            f"CREATE TABLE {target} AS SELECT {projected} "
            f"FROM read_csv_auto('{path.as_posix()}', header=true)")
        counts[t] = con.execute(f"SELECT COUNT(*) FROM {target}").fetchone()[0]
    return counts


def migrate_gold(con) -> dict[str, int]:
    defs = {
        "sales_by_sku": (
            "SELECT sku, SUM(quantity_kg) AS total_sold_kg, "
            "ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS sales_value_myr, "
            "COUNT(*) AS transaction_count "
            "FROM lake.silver.transactions WHERE txn_type = 'OUT' GROUP BY sku"),
        "capacity_by_location": (
            "SELECT location_code, location_name, current_load_kg, capacity_kg, "
            "ROUND(100.0 * current_load_kg / capacity_kg, 1) AS pct_used, "
            "capacity_kg - current_load_kg AS free_kg "
            "FROM lake.silver.locations"),
        "supplier_risk": (
            "SELECT supplier_id, supplier_name, country, risk_score, lead_time_days, "
            "ROUND((risk_score * 0.65) + ((lead_time_days / 60.0) * 0.35), 3) AS ranking_score "
            "FROM lake.silver.suppliers"),
    }
    counts: dict[str, int] = {}
    for name, sql in defs.items():
        target = f"lake.gold.{name}"
        con.execute(f"DROP TABLE IF EXISTS {target}")
        con.execute(f"CREATE TABLE {target} AS {sql}")
        counts[name] = con.execute(f"SELECT COUNT(*) FROM {target}").fetchone()[0]
    return counts


def migrate_all() -> dict:
    con = connect()
    try:
        bronze = migrate_bronze(con)
        silver = migrate_silver(con)
        gold = migrate_gold(con)
    finally:
        con.close()
    try:
        from packs.dms.audit import ledger

        ledger.append("system", "lakehouse.migrated",
                      {"mode": lakehouse_mode(),
                       "bronze": sum(bronze.values()),
                       "silver": sum(silver.values()),
                       "gold": sum(gold.values())})
    except Exception:  # noqa: BLE001
        pass
    return {"mode": lakehouse_mode(), "bronze": bronze, "silver": silver, "gold": gold}


def main() -> None:
    result = migrate_all()
    print(f"lakehouse mode: {result['mode']}")
    for layer in ("bronze", "silver", "gold"):
        total = sum(result[layer].values())
        print(f"  {layer}: {total} rows across {len(result[layer])} tables")
    print(f"tables now: {lt.list_tables()}")


if __name__ == "__main__":
    main()
