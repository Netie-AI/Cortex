"""S0 — stream registry (ops DB, the OLTP metadata plane; mirrors Databricks'
Lakebase/warehouse split). CRUD for declared streams."""
from __future__ import annotations

import datetime as _dt
import re
import sqlite3
from typing import Any

from packs.dms.audit.ledger import default_db_path

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(default_db_path()))
    con.row_factory = sqlite3.Row
    return con


def _ensure(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS dms_streams ("
        " stream_id TEXT PRIMARY KEY, name TEXT, created_by TEXT,"
        " schema_hint TEXT, status TEXT, created_at TEXT)"
    )


def valid_stream_id(stream_id: str) -> bool:
    return bool(_ID_RE.match(stream_id or ""))


def create_stream(stream_id: str, *, name: str = "", created_by: str = "system",
                  schema_hint: str = "") -> dict[str, Any]:
    if not valid_stream_id(stream_id):
        raise ValueError(f"invalid stream_id {stream_id!r} (lowercase alnum/_/-, <=63 chars)")
    con = _conn()
    try:
        _ensure(con)
        existing = con.execute(
            "SELECT stream_id FROM dms_streams WHERE stream_id = ?", (stream_id,)).fetchone()
        if existing:
            return get_stream(stream_id)  # idempotent create
        con.execute(
            "INSERT INTO dms_streams VALUES (?,?,?,?,?,?)",
            (stream_id, name or stream_id, created_by, schema_hint, "active", _now()))
        con.commit()
    finally:
        con.close()
    _audit("stream.created", {"stream_id": stream_id, "created_by": created_by})
    return get_stream(stream_id)


def get_stream(stream_id: str) -> dict[str, Any] | None:
    con = _conn()
    try:
        _ensure(con)
        row = con.execute(
            "SELECT * FROM dms_streams WHERE stream_id = ?", (stream_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def list_streams() -> list[dict[str, Any]]:
    con = _conn()
    try:
        _ensure(con)
        return [dict(r) for r in con.execute(
            "SELECT * FROM dms_streams ORDER BY created_at DESC").fetchall()]
    finally:
        con.close()


def set_status(stream_id: str, status: str) -> None:
    con = _conn()
    try:
        _ensure(con)
        con.execute("UPDATE dms_streams SET status = ? WHERE stream_id = ?", (status, stream_id))
        con.commit()
    finally:
        con.close()


def _audit(event: str, payload: dict) -> None:
    try:
        from packs.dms.audit import ledger

        ledger.append(payload.get("created_by", "system"), event, payload)
    except Exception:  # noqa: BLE001
        pass
