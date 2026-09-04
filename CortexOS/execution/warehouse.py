"""Serving-warehouse DuckDB connections — the only production DuckDB open site.

C4: every warehouse connection in CortexOS goes through this module so
``enforce_manifest`` / ``submit`` can sit in front of reads. Callers outside
``CortexOS.execution`` must not ``import duckdb``.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path(os.environ.get("DMS_WAREHOUSE_DB") or ROOT / "data" / "dms_demo.duckdb")

_RO_LOCK = threading.Lock()
_RO_CONNECTIONS: dict[str, Any] = {}


def warehouse_path(db_path: Path | str | None = None) -> Path:
    """Resolve the serving warehouse. Re-reads ``DMS_WAREHOUSE_DB`` each call.

    ``DEFAULT_DB`` stays an import-time snapshot for callers that monkeypatch
    the constant. Loaders go through this function so a later env change
    (tests, a second process) is not stuck on the import-time path.
    """
    if db_path is not None:
        return Path(db_path)
    env = os.environ.get("DMS_WAREHOUSE_DB")
    if env:
        return Path(env)
    return Path(DEFAULT_DB)


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
    "close_cached_connections",
    "connect_write",
    "get_connection",
    "read_only_queries_enabled",
    "warehouse_path",
]
