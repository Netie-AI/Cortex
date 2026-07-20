"""OpenDMS lakehouse (Feature L0) — DuckLake-backed bronze/silver/gold.

One open format (Parquet + SQL catalog) with ACID, time travel, and schema
evolution — local-first, zero infrastructure. Falls back to plain DuckDB
schemas when the ducklake extension cannot be installed (offline CI), always
honestly reporting which mode is live via `lakehouse_mode`.

See docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md Feature L0 and
docs/research/findings/LAKEHOUSE_2026.md.
"""
from packs.dms.lakehouse.catalog import (
    LakehouseMode,
    connect,
    lakehouse_mode,
    lakehouse_status,
)

__all__ = ["LakehouseMode", "connect", "lakehouse_mode", "lakehouse_status"]
