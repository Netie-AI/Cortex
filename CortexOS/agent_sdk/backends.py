"""Per-pack query backends for the Agent SDK.

A backend answers "give me rows of <object_type>" for one pack's data store.
The SDK resolves visibility/RBAC BEFORE the backend runs — a backend only ever
sees pre-filtered, agent-visible columns and validated filter keys.

Packs register theirs via ``register_query_backend(pack, fn)``. The DMS DuckDB
warehouse backend ships as the reference implementation and registers lazily.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Protocol

from CortexOS.execution.manifest import VerifiedManifest

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class QueryBackend(Protocol):
    def __call__(
        self,
        object_type: str,
        columns: list[str],
        filters: dict[str, Any],
        limit: int,
        *,
        verified: VerifiedManifest,
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
    verified: VerifiedManifest | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Reference backend: the DMS DuckDB warehouse (object type id == table name).

    Every read goes through ``execute_sql`` -> ``enforce_manifest``. Missing
    verified is a hard refuse — this backend never opens an ungoverned connection.
    """
    from CortexOS.execution.session_manifests import SessionUnbound
    from CortexOS.execution.submit import execute_sql
    from CortexOS.execution.warehouse import warehouse_path

    if verified is None:
        raise SessionUnbound("manifest is required for warehouse reads")

    root = Path(__file__).resolve().parents[2]
    path = (
        Path(db_path)
        if db_path is not None
        else warehouse_path(
            os.environ.get("DMS_WAREHOUSE_DB") or root / "data" / "dms_demo.duckdb"
        )
    )
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
    rows, _, _ = execute_sql(
        verified, sql, params=values or None, db_path=path
    )
    return rows
