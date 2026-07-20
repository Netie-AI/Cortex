"""L0 — DuckLake catalog attach + schema bootstrap, with honest fallback.

The lakehouse is a DuckLake catalog (SQLite catalog DB now, Postgres later —
same data files, no migration) holding bronze/silver/gold schemas of Parquet.
When the `ducklake` extension can't be installed (air-gapped CI), we fall back
to plain DuckDB schemas in a single file and report `lakehouse_mode="fallback"`
so nothing ever claims time-travel it can't deliver.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

LakehouseMode = Literal["ducklake", "fallback"]

ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_HOME = ROOT / "data" / "lakehouse"

SCHEMAS: tuple[str, ...] = ("bronze", "silver", "gold")
LAKE_ALIAS = "lake"

# Cache the detected capability once per process; probing installs an extension.
_MODE_CACHE: LakehouseMode | None = None


def lakehouse_home() -> Path:
    """Root dir for catalog + Parquet data. Overridable for isolation/tests."""
    return Path(os.environ.get("DMS_LAKEHOUSE_HOME") or _DEFAULT_HOME)


def _catalog_path(home: Path) -> Path:
    return home / "catalog.sqlite"


def _data_path(home: Path) -> Path:
    return home / "data"


def _fallback_path(home: Path) -> Path:
    return home / "fallback.duckdb"


def _ducklake_available() -> bool:
    global _MODE_CACHE
    if _MODE_CACHE is not None:
        return _MODE_CACHE == "ducklake"
    if os.environ.get("DMS_LAKEHOUSE_FORCE_FALLBACK", "").lower() in ("1", "true", "yes"):
        _MODE_CACHE = "fallback"
        return False
    try:
        import duckdb

        con = duckdb.connect()
        try:
            con.execute("INSTALL ducklake")
            con.execute("LOAD ducklake")
        finally:
            con.close()
        _MODE_CACHE = "ducklake"
        return True
    except Exception:
        _MODE_CACHE = "fallback"
        return False


def lakehouse_mode() -> LakehouseMode:
    return "ducklake" if _ducklake_available() else "fallback"


def connect(*, home: Path | str | None = None, read_only: bool = False):
    """Return a DuckDB connection with the lakehouse attached as ``lake``.

    In ducklake mode: ATTACH the DuckLake catalog. In fallback mode: attach a
    plain DuckDB file as ``lake`` so callers use the same ``lake.<schema>.<t>``
    names either way. Schemas are ensured on first connect.
    """
    import duckdb

    home = Path(home) if home else lakehouse_home()
    home.mkdir(parents=True, exist_ok=True)

    if _ducklake_available():
        _data_path(home).mkdir(parents=True, exist_ok=True)
        con = duckdb.connect()
        con.execute("INSTALL ducklake")
        con.execute("LOAD ducklake")
        cat = _catalog_path(home).as_posix()
        data = _data_path(home).as_posix()
        ro = ", READ_ONLY" if read_only else ""
        con.execute(
            f"ATTACH 'ducklake:sqlite:{cat}' AS {LAKE_ALIAS} (DATA_PATH '{data}'{ro})"
        )
    else:
        con = duckdb.connect()
        fb = _fallback_path(home).as_posix()
        ro = " (READ_ONLY)" if read_only else ""
        con.execute(f"ATTACH '{fb}' AS {LAKE_ALIAS}{ro}")

    if not read_only:
        for schema in SCHEMAS:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {LAKE_ALIAS}.{schema}")
    return con


def lakehouse_status(*, home: Path | str | None = None) -> dict:
    """Capability + inventory summary for /api/engine/specs and the Studio."""
    home = Path(home) if home else lakehouse_home()
    mode = lakehouse_mode()
    status: dict = {
        "lakehouse_mode": mode,
        "time_travel": mode == "ducklake",
        "schema_evolution": True,
        "home": str(home),
        "catalog": "sqlite",
        "schemas": {},
    }
    if not home.exists():
        return status
    try:
        con = connect(home=home, read_only=True)
    except Exception as exc:  # noqa: BLE001 — status must never raise
        status["error"] = repr(exc)
        return status
    try:
        for schema in SCHEMAS:
            rows = con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog = ? AND table_schema = ? ORDER BY table_name",
                [LAKE_ALIAS, schema],
            ).fetchall()
            status["schemas"][schema] = [r[0] for r in rows]
    except Exception as exc:  # noqa: BLE001
        status["error"] = repr(exc)
    finally:
        con.close()
    return status


def reset_mode_cache() -> None:
    """Test hook: forget the probed capability so env overrides re-apply."""
    global _MODE_CACHE
    _MODE_CACHE = None
