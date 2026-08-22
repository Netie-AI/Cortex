"""Serving-warehouse DuckDB connections — the only production DuckDB open site.

C4: every warehouse connection in CortexOS goes through this module so
``enforce_manifest`` / ``submit`` can sit in front of reads. Callers outside
``CortexOS.execution`` must not ``import duckdb``.

Path contract (Cortex#14 / TAS-DMS): both products honor ``DMS_WAREHOUSE_DB``.
Resolved at *call* time — an import-time snapshot made Cortex answer
``<Cortex>/data/dms_demo.duckdb`` while DMS Studio wrote
``<DMS>/data/dms_demo.duckdb``. Unset env keeps the in-repo demo file
(documented demo grant; unbound ``/dms/query`` still serves demo revenue).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from CortexOS.paths import data_path

WAREHOUSE_DB_ENV = "DMS_WAREHOUSE_DB"
FALLBACK_DB_NAME = "dms_demo.duckdb"
FALLBACK_DB = data_path(FALLBACK_DB_NAME)
# Back-compat alias: the no-env demo file. Opens go through warehouse_path()
# so a later DMS_WAREHOUSE_DB still wins (import-time DEFAULT_DB did not).
DEFAULT_DB = FALLBACK_DB

_RO_LOCK = threading.Lock()
_RO_CONNECTIONS: dict[str, Any] = {}


def warehouse_path(db_path: Path | str | None = None) -> Path:
    """Resolve the serving DuckDB path.

    Explicit paths that are *not* the repo fallback win (tests pass a tmp
    file). The fallback and ``None`` re-read ``DMS_WAREHOUSE_DB`` so
    ``get_connection(DEFAULT_DB)`` after a late env set is not a silent miss.
    """
    env = (os.environ.get(WAREHOUSE_DB_ENV) or "").strip()
    if db_path is None or Path(db_path) == FALLBACK_DB:
        return Path(env) if env else FALLBACK_DB
    return Path(db_path)


def read_only_queries_enabled() -> bool:
    """Whether query paths should open read-only (opt-in; see warehouse_db docs)."""
    return os.environ.get("DMS_READ_ONLY_QUERIES", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def get_connection(db_path: Path | str | None = None, *, read_only: bool = False) -> Any:
    """Open the warehouse DuckDB.

    Pass ``read_only=True`` from every governed query path. Read-only connections
    share the file; read-write takes an exclusive lock.
    """
    import duckdb

    path = warehouse_path(db_path)
    if read_only and path.exists():
        cursor = _read_only_cursor(path)
        if cursor is not None:
            return cursor
    else:
        _evict_read_only(path)
    return duckdb.connect(str(path))


def _read_only_cursor(path: Path) -> Any | None:
    key = str(path)
    with _RO_LOCK:
        parent = _RO_CONNECTIONS.get(key)
        if parent is None:
            import duckdb

            try:
                parent = duckdb.connect(key, read_only=True)
            except Exception:  # noqa: BLE001
                return None
            _RO_CONNECTIONS[key] = parent
    try:
        return parent.cursor()
    except Exception:  # noqa: BLE001
        with _RO_LOCK:
            _RO_CONNECTIONS.pop(key, None)
        return None


def _evict_read_only(path: Path) -> None:
    key = str(path)
    with _RO_LOCK:
        con = _RO_CONNECTIONS.pop(key, None)
    if con is not None:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def close_cached_connections() -> None:
    """Release cached read-only instances (tests, and before a reload)."""
    with _RO_LOCK:
        for con in _RO_CONNECTIONS.values():
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
        _RO_CONNECTIONS.clear()


def connect_write(db_path: Path | str | None = None) -> Any:
    """Open a read-write connection (loaders / seeders only). Evicts RO cache."""
    import duckdb

    path = warehouse_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _evict_read_only(path)
    return duckdb.connect(str(path))


__all__ = [
    "DEFAULT_DB",
    "FALLBACK_DB",
    "WAREHOUSE_DB_ENV",
    "close_cached_connections",
    "connect_write",
    "get_connection",
    "read_only_queries_enabled",
    "warehouse_path",
]
