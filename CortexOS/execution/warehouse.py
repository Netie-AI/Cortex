"""Serving-warehouse DuckDB connections — the only production DuckDB open site.

C4: every warehouse connection in CortexOS goes through this module so
``enforce_manifest`` / ``submit`` can sit in front of reads. Callers outside
``CortexOS.execution`` must not ``import duckdb``.
"""

from __future__ import annotations

import itertools
import os
import threading
import weakref
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path(os.environ.get("DMS_WAREHOUSE_DB") or ROOT / "data" / "dms_demo.duckdb")

_RO_LOCK = threading.Lock()
# path -> (cached read-only parent, its instance number). A number is never reused.
_RO_CONNECTIONS: dict[str, tuple[Any, int]] = {}
_RO_NUMBERS = itertools.count(1)
# Each cursor handed out from a cached parent -> that parent's (path, number).
_CURSOR_INSTANCE: weakref.WeakKeyDictionary[Any, tuple[str, int]] = weakref.WeakKeyDictionary()


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
        cached = _RO_CONNECTIONS.get(key)
        if cached is None:
            import duckdb

            try:
                parent = duckdb.connect(key, read_only=True)
            except Exception:  # noqa: BLE001
                return None
            cached = (parent, next(_RO_NUMBERS))
            _RO_CONNECTIONS[key] = cached
        parent, number = cached
        try:
            cursor = parent.cursor()
        except Exception:  # noqa: BLE001
            if _RO_CONNECTIONS.get(key) is cached:
                _RO_CONNECTIONS.pop(key, None)
            return None
        _CURSOR_INSTANCE[cursor] = (key, number)
    return cursor


def connection_instance(con: Any) -> tuple[str, int] | None:
    """The read-only database instance ``con`` reads, as ``(path, number)``, or None.

    Named only for a cursor this module handed out from its cached read-only
    instance. That instance reads the file it opened for as long as it lives:
    no writer can take the file while it is open, a rebuild in this process
    (``connect_write``, a read-write ``get_connection``) evicts it first, and a
    file renamed over the path is read only by a later instance. So what a
    check finds on that instance stays true for every cursor of it. The number
    is never reused; a later instance on the same path is a different name even
    though the path, and the file's stamp, may say nothing changed.

    None for anything else (a read-write or in-memory connection, one opened
    elsewhere): its data can change under it, so a caller must not reuse a
    verdict about it.
    """
    try:
        with _RO_LOCK:
            return _CURSOR_INSTANCE.get(con)
    except TypeError:  # not weak-referenceable or not hashable: not one of ours
        return None


def _evict_read_only(path: Path) -> None:
    key = str(path)
    with _RO_LOCK:
        cached = _RO_CONNECTIONS.pop(key, None)
    if cached is not None:
        try:
            cached[0].close()
        except Exception:  # noqa: BLE001
            pass


def close_cached_connections() -> None:
    """Release cached read-only instances (tests, and before a reload)."""
    with _RO_LOCK:
        for parent, _number in _RO_CONNECTIONS.values():
            try:
                parent.close()
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
    "connection_instance",
    "get_connection",
    "read_only_queries_enabled",
    "warehouse_path",
]
