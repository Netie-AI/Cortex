"""Per-pack query backends for the Agent SDK.

A backend answers "give me rows of <object_type>" for one pack's data store.
The SDK resolves visibility/RBAC BEFORE the backend runs — a backend only ever
sees pre-filtered, agent-visible columns and validated filter keys.

Packs register theirs via ``register_query_backend(pack, fn)``. The DMS DuckDB
warehouse backend ships as the reference implementation and registers lazily.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class QueryBackend(Protocol):
    def __call__(
        self,
        object_type: str,
        columns: list[str],
        filters: dict[str, Any],
        limit: int,
        *,
        db_path: Path | str | None = None,
    ) -> list[dict[str, Any]]: ...


_BACKENDS: dict[str, QueryBackend] = {}


def register_query_backend(pack: str, backend: QueryBackend) -> None:
    _BACKENDS[pack] = backend


def get_query_backend(pack: str) -> QueryBackend:
    if pack not in _BACKENDS and pack == "dms":
        register_query_backend("dms", _dms_duckdb_backend)
    try:
        return _BACKENDS[pack]
    except KeyError:
        raise LookupError(
            f"no query backend registered for pack {pack!r} — "
            "call CortexOS.agent_sdk.register_query_backend(pack, fn)"
        ) from None


def _quote(ident: str) -> str:
    if not _IDENT.match(ident):
        raise ValueError(f"unsafe identifier: {ident!r}")
    return f'"{ident}"'


def _dms_duckdb_backend(
    object_type: str,
    columns: list[str],
    filters: dict[str, Any],
    limit: int,
    *,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Reference backend: the DMS DuckDB warehouse (object type id == table name).

    Opens via ``CortexOS.execution.warehouse`` only — no direct ``duckdb`` import
    (C4). Ungoverned agent reads remain a C4.follow concern relative to
    ``enforce_manifest``; the AST invariant is what this change closes.
    """
    from CortexOS.execution.warehouse import get_connection, warehouse_path

    path = warehouse_path(db_path)
    cols_sql = ", ".join(_quote(c) for c in columns)
    where_sql = ""
    values: list[Any] = []
    if filters:
        parts = []
        for key, value in filters.items():
            parts.append(f"{_quote(key)} = ?")
            values.append(value)
        where_sql = " WHERE " + " AND ".join(parts)
    sql = f"SELECT {cols_sql} FROM {_quote(object_type)}{where_sql} LIMIT {int(limit)}"
    con = get_connection(path, read_only=True)
    try:
        rel = con.execute(sql, values)
        names = [d[0] for d in rel.description]
        return [dict(zip(names, row, strict=False)) for row in rel.fetchall()]
    finally:
        con.close()
