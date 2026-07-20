"""L0 — table read/write, snapshots, time-travel, and a schema-evolution guard.

All writes go through here so every mutation lands one F1 ledger event and the
evolution guard blocks silent drop/rename (additive-only unless forced).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from packs.dms.lakehouse.catalog import LAKE_ALIAS, SCHEMAS, connect, lakehouse_mode

_IDENT_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


class LakehouseError(RuntimeError):
    pass


@dataclass(slots=True)
class Snapshot:
    snapshot_id: int
    ts: str
    changes: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"snapshot_id": self.snapshot_id, "ts": self.ts,
                "changes": self.changes, "message": self.message}


def _ident(name: str) -> str:
    if not name or any(ch not in _IDENT_OK for ch in name):
        raise LakehouseError(f"unsafe identifier: {name!r}")
    return name


def _check_schema(schema: str) -> str:
    if schema not in SCHEMAS:
        raise LakehouseError(f"unknown schema {schema!r} (allowed: {SCHEMAS})")
    return schema


def _qualified(schema: str, name: str) -> str:
    return f"{LAKE_ALIAS}.{_check_schema(schema)}.{_ident(name)}"


def _ledger(event_type: str, payload: dict[str, Any], *, actor: str) -> None:
    try:
        from packs.dms.audit import ledger

        ledger.append(actor, event_type, payload)
    except Exception:  # noqa: BLE001 — ledger must never break a data write in dev
        pass


def write_table(
    schema: str,
    name: str,
    *,
    select_sql: str | None = None,
    rows: Sequence[dict] | None = None,
    con=None,
    actor: str = "system",
    replace: bool = True,
) -> int:
    """Create/replace ``lake.<schema>.<name>`` from a SELECT or a list of dicts.

    Returns the resulting row count. Emits ``lakehouse.table_written``.
    """
    if (select_sql is None) == (rows is None):
        raise LakehouseError("exactly one of select_sql or rows required")

    owns = con is None
    con = con or connect()
    target = _qualified(schema, name)
    try:
        if replace:
            con.execute(f"DROP TABLE IF EXISTS {target}")
        if select_sql is not None:
            con.execute(f"CREATE TABLE {target} AS {select_sql}")
        else:
            _create_from_rows(con, target, rows or [])
        count = con.execute(f"SELECT COUNT(*) FROM {target}").fetchone()[0]
    finally:
        if owns:
            con.close()
    _ledger("lakehouse.table_written",
            {"schema": schema, "table": name, "rows": int(count), "mode": lakehouse_mode()},
            actor=actor)
    return int(count)


_PYTYPE_TO_DUCK = {bool: "BOOLEAN", int: "BIGINT", float: "DOUBLE", str: "VARCHAR"}


def _duck_type(rows: Sequence[dict], col: str) -> str:
    for r in rows:
        v = r.get(col)
        if v is not None:
            return _PYTYPE_TO_DUCK.get(type(v), "VARCHAR")
    return "VARCHAR"


def _create_from_rows(con, target: str, rows: Sequence[dict]) -> None:
    """Portable rows→table with no pandas/pyarrow dependency: infer DuckDB types
    from the first non-null value per column, CREATE, then executemany INSERT."""
    if not rows:
        raise LakehouseError("cannot infer schema from zero rows")
    cols = list(rows[0].keys())
    for c in cols:
        _ident(c)
    coldefs = ", ".join(f"{c} {_duck_type(rows, c)}" for c in cols)
    con.execute(f"CREATE TABLE {target} ({coldefs})")
    placeholders = ", ".join(["?"] * len(cols))
    con.executemany(
        f"INSERT INTO {target} ({', '.join(cols)}) VALUES ({placeholders})",
        [[r.get(c) for c in cols] for r in rows],
    )


def append_rows(schema: str, name: str, rows: Sequence[dict], *,
                con=None, actor: str = "system") -> int:
    owns = con is None
    con = con or connect()
    target = _qualified(schema, name)
    try:
        existing = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_catalog=? AND table_schema=? AND table_name=?",
            [LAKE_ALIAS, schema, name]).fetchall()}
        if not existing:
            raise LakehouseError(f"{schema}.{name} does not exist; use write_table first")
        cols = [c for c in rows[0].keys() if c in existing] if rows else []
        for c in cols:
            _ident(c)
        placeholders = ", ".join(["?"] * len(cols))
        collist = ", ".join(cols)
        con.executemany(
            f"INSERT INTO {target} ({collist}) VALUES ({placeholders})",
            [[r.get(c) for c in cols] for r in rows],
        )
        count = len(rows)
    finally:
        if owns:
            con.close()
    _ledger("lakehouse.rows_appended", {"schema": schema, "table": name, "rows": count}, actor=actor)
    return count


def read(schema: str, name: str, *, con=None, limit: int | None = None) -> list[dict]:
    owns = con is None
    con = con or connect(read_only=True)
    target = _qualified(schema, name)
    try:
        sql = f"SELECT * FROM {target}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row)) for row in rel.fetchall()]
    finally:
        if owns:
            con.close()


def snapshots(schema: str, name: str, *, con=None) -> list[Snapshot]:
    """Return DuckLake snapshots (empty list in fallback mode — no lineage there)."""
    if lakehouse_mode() != "ducklake":
        return []
    owns = con is None
    con = con or connect(read_only=True)
    _check_schema(schema)
    _ident(name)
    try:
        rows = con.execute(
            f"SELECT snapshot_id, snapshot_time, changes, commit_message "
            f"FROM ducklake_snapshots('{LAKE_ALIAS}') ORDER BY snapshot_id"
        ).fetchall()
        return [Snapshot(int(r[0]), str(r[1]), _stringify(r[2]), r[3]) for r in rows]
    finally:
        if owns:
            con.close()


def _stringify(val: Any) -> str | None:
    return None if val is None else str(val)


def query_at(snapshot_id: int, schema: str, name: str, *, con=None,
             columns: str = "*", where: str = "") -> list[dict]:
    """Time-travel read of a table at a specific snapshot (ducklake mode only)."""
    if lakehouse_mode() != "ducklake":
        raise LakehouseError("time travel requires ducklake mode (this box is in fallback)")
    owns = con is None
    con = con or connect(read_only=True)
    target = _qualified(schema, name)
    clause = f" WHERE {where}" if where else ""
    try:
        rel = con.execute(f"SELECT {columns} FROM {target} AT (VERSION => {int(snapshot_id)}){clause}")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row)) for row in rel.fetchall()]
    finally:
        if owns:
            con.close()


def add_column(schema: str, name: str, column: str, sql_type: str, *,
               con=None, actor: str = "system") -> None:
    """Additive schema evolution — the only mutation the guard allows silently."""
    owns = con is None
    con = con or connect()
    target = _qualified(schema, name)
    _ident(column)
    if any(ch not in _IDENT_OK | set(" ()") for ch in sql_type):
        raise LakehouseError(f"unsafe type: {sql_type!r}")
    try:
        con.execute(f"ALTER TABLE {target} ADD COLUMN {column} {sql_type}")
    finally:
        if owns:
            con.close()
    _ledger("lakehouse.column_added", {"schema": schema, "table": name, "column": column}, actor=actor)


def drop_column(schema: str, name: str, column: str, *, con=None,
                actor: str = "system", force: bool = False) -> None:
    """Destructive evolution — blocked unless force=True (guardrail)."""
    if not force:
        raise LakehouseError(
            f"dropping {schema}.{name}.{column} is destructive; pass force=True to confirm")
    owns = con is None
    con = con or connect()
    target = _qualified(schema, name)
    _ident(column)
    try:
        con.execute(f"ALTER TABLE {target} DROP COLUMN {column}")
    finally:
        if owns:
            con.close()
    _ledger("lakehouse.column_dropped", {"schema": schema, "table": name, "column": column}, actor=actor)


def list_tables(*, con=None) -> dict[str, list[str]]:
    owns = con is None
    con = con or connect(read_only=True)
    try:
        out: dict[str, list[str]] = {}
        for schema in SCHEMAS:
            rows = con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog=? AND table_schema=? ORDER BY table_name",
                [LAKE_ALIAS, schema]).fetchall()
            out[schema] = [r[0] for r in rows]
        return out
    finally:
        if owns:
            con.close()
