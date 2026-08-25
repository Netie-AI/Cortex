"""Tamper-evident hash-chained audit ledger (F1 spine for DMS)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64
_LOCK = threading.Lock()
_POSTGRES_MIGRATION = Path(__file__).resolve().parents[1] / "sql" / "002_ledger_postgres.sql"
_pg_engine = None
_pg_engine_dsn: str | None = None
_pg_schema_ready = False

# RLS session GUCs — set ONLY from authenticated Caller (never client body fields).
_rls_role: ContextVar[str] = ContextVar("dms_rls_role", default="admin")
_rls_tenant: ContextVar[str] = ContextVar("dms_rls_tenant", default="default")


def set_rls_context(*, role: str, tenant_id: str = "default") -> None:
    """Stamp request-scoped Postgres RLS GUCs from api_auth.Caller."""
    r = (role or "viewer").strip().lower()
    if r not in ("viewer", "steward", "admin"):
        r = "viewer"
    _rls_role.set(r)
    _rls_tenant.set((tenant_id or "default").strip() or "default")


def _stamp_rls(conn) -> None:
    """Apply app.role / app.tenant_id on a live Postgres connection."""
    from sqlalchemy import text

    conn.execute(text("SELECT set_config('app.role', :r, true)"), {"r": _rls_role.get()})
    conn.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"),
        {"t": _rls_tenant.get()},
    )


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    id: str
    seq: int
    actor: str
    event_type: str
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str
    created_at: str
    signature: str | None = None


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    broken_at: int | None = None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_db_path() -> Path:
    env = os.environ.get("DMS_OPS_DB")
    if env:
        return Path(env)
    return repo_root() / "data" / "dms_ops.db"


def ledger_dsn() -> str | None:
    dsn = os.environ.get("DMS_LEDGER_DSN")
    return dsn.strip() if dsn else None


def pg_advisory_lock_key() -> int:
    digest = hashlib.sha256(b"dms_audit_ledger").digest()
    return int.from_bytes(digest[:8], "big", signed=False) % (2**63)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_entry_hash(seq: int, prev_hash: str, payload: dict[str, Any], created_at_iso: str) -> str:
    body = f"{seq}|{prev_hash}|{canonical_json(payload)}|{created_at_iso}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_ledger_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS dms_audit_ledger (
            id TEXT PRIMARY KEY,
            seq INTEGER NOT NULL UNIQUE,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            entry_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            signature TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dms_audit_seq ON dms_audit_ledger(seq);
        """
    )
    con.commit()


def _row_to_entry(row: sqlite3.Row | dict[str, Any]) -> LedgerEntry:
    if isinstance(row, sqlite3.Row):
        payload_raw = row["payload"]
        return LedgerEntry(
            id=row["id"],
            seq=int(row["seq"]),
            actor=row["actor"],
            event_type=row["event_type"],
            payload=json.loads(payload_raw),
            prev_hash=row["prev_hash"],
            entry_hash=row["entry_hash"],
            created_at=row["created_at"],
            signature=row["signature"],
        )
    payload_raw = row["payload"]
    if isinstance(payload_raw, dict):
        payload = payload_raw
    else:
        payload = json.loads(payload_raw)
    return LedgerEntry(
        id=str(row["id"]),
        seq=int(row["seq"]),
        actor=row["actor"],
        event_type=row["event_type"],
        payload=payload,
        prev_hash=row["prev_hash"],
        entry_hash=row["entry_hash"],
        created_at=row["created_at"],
        signature=row["signature"],
    )


def _resolve_sqlite_path(db_path: Path | str | None) -> Path:
    return Path(db_path or default_db_path())


def _get_postgres_engine(dsn: str):
    global _pg_engine, _pg_engine_dsn
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise RuntimeError(
            "DMS_LEDGER_DSN is set but sqlalchemy is not installed; "
            "install with pip install 'netie[postgres]'"
        ) from exc
    if _pg_engine is None or _pg_engine_dsn != dsn:
        _pg_engine = create_engine(dsn, pool_pre_ping=True)
        _pg_engine_dsn = dsn
    return _pg_engine


def _init_postgres_schema(engine) -> None:
    global _pg_schema_ready
    if _pg_schema_ready:
        return
    sql = _POSTGRES_MIGRATION.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)
    _pg_schema_ready = True


def _sqlite_append(
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    db_path: Path,
) -> LedgerEntry:
    with _LOCK:
        con = _connect(db_path)
        try:
            init_ledger_schema(con)
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT seq, entry_hash FROM dms_audit_ledger ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if row is None:
                seq = 0
                prev_hash = GENESIS_HASH
            else:
                seq = int(row["seq"]) + 1
                prev_hash = row["entry_hash"]
            created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            entry_hash = compute_entry_hash(seq, prev_hash, payload, created_at)
            entry_id = str(uuid.uuid4())
            con.execute(
                """
                INSERT INTO dms_audit_ledger
                (id, seq, actor, event_type, payload, prev_hash, entry_hash, created_at, signature)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    entry_id,
                    seq,
                    actor,
                    event_type,
                    canonical_json(payload),
                    prev_hash,
                    entry_hash,
                    created_at,
                ),
            )
            con.commit()
            return LedgerEntry(
                id=entry_id,
                seq=seq,
                actor=actor,
                event_type=event_type,
                payload=payload,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
                created_at=created_at,
            )
        finally:
            con.close()


def _postgres_append(actor: str, event_type: str, payload: dict[str, Any]) -> LedgerEntry:
    from sqlalchemy import text

    dsn = ledger_dsn()
    if not dsn:
        raise RuntimeError("Postgres append requested without DMS_LEDGER_DSN")
    engine = _get_postgres_engine(dsn)
    _init_postgres_schema(engine)
    lock_key = pg_advisory_lock_key()
    with engine.begin() as conn:
        _stamp_rls(conn)
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        row = conn.execute(
            text(
                """
                SELECT seq, entry_hash
                FROM dms_audit_ledger
                ORDER BY seq DESC
                LIMIT 1
                FOR UPDATE
                """
            )
        ).mappings().first()
        if row is None:
            seq = 0
            prev_hash = GENESIS_HASH
        else:
            seq = int(row["seq"]) + 1
            prev_hash = row["entry_hash"]
        created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        entry_hash = compute_entry_hash(seq, prev_hash, payload, created_at)
        entry_id = str(uuid.uuid4())
        conn.execute(
            text(
                """
                INSERT INTO dms_audit_ledger
                (id, seq, actor, event_type, payload, prev_hash, entry_hash, created_at, signature)
                VALUES (:id, :seq, :actor, :event_type, :payload, :prev_hash, :entry_hash, :created_at, NULL)
                """
            ),
            {
                "id": entry_id,
                "seq": seq,
                "actor": actor,
                "event_type": event_type,
                "payload": canonical_json(payload),
                "prev_hash": prev_hash,
                "entry_hash": entry_hash,
                "created_at": created_at,
            },
        )
    return LedgerEntry(
        id=entry_id,
        seq=seq,
        actor=actor,
        event_type=event_type,
        payload=payload,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        created_at=created_at,
    )


def append(
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    db_path: Path | str | None = None,
) -> LedgerEntry:
    dsn = ledger_dsn()
    if dsn:
        return _postgres_append(actor, event_type, payload)
    return _sqlite_append(actor, event_type, payload, db_path=_resolve_sqlite_path(db_path))


def _sqlite_verify(*, db_path: Path, start_seq: int) -> VerifyResult:
    con = _connect(db_path)
    try:
        init_ledger_schema(con)
        rows = con.execute(
            "SELECT * FROM dms_audit_ledger WHERE seq >= ? ORDER BY seq ASC",
            (start_seq,),
        ).fetchall()
        prev_hash = GENESIS_HASH if start_seq == 0 else None
        if start_seq > 0:
            prior = con.execute(
                "SELECT entry_hash FROM dms_audit_ledger WHERE seq = ?",
                (start_seq - 1,),
            ).fetchone()
            prev_hash = prior["entry_hash"] if prior else GENESIS_HASH
        if not rows:
            if start_seq > 0:
                # Fail closed: asking to verify past the tip is not "ok".
                return VerifyResult(ok=False, broken_at=start_seq)
            return VerifyResult(ok=True)
        first = int(rows[0]["seq"])
        if start_seq == 0 and first != 0:
            return VerifyResult(ok=False, broken_at=first)
        expected_seq = start_seq if start_seq > 0 else first
        for row in rows:
            seq = int(row["seq"])
            if seq != expected_seq:
                return VerifyResult(ok=False, broken_at=seq)
            payload = json.loads(row["payload"])
            expected = compute_entry_hash(seq, prev_hash or GENESIS_HASH, payload, row["created_at"])
            if row["entry_hash"] != expected or row["prev_hash"] != (prev_hash or GENESIS_HASH):
                return VerifyResult(ok=False, broken_at=seq)
            prev_hash = row["entry_hash"]
            expected_seq = seq + 1
        return VerifyResult(ok=True)
    finally:
        con.close()


def _postgres_verify(*, start_seq: int) -> VerifyResult:
    from sqlalchemy import text

    dsn = ledger_dsn()
    if not dsn:
        raise RuntimeError("Postgres verify requested without DMS_LEDGER_DSN")
    engine = _get_postgres_engine(dsn)
    _init_postgres_schema(engine)
    with engine.connect() as conn:
        _stamp_rls(conn)
        rows = conn.execute(
            text("SELECT * FROM dms_audit_ledger WHERE seq >= :start_seq ORDER BY seq ASC"),
            {"start_seq": start_seq},
        ).mappings().all()
        prev_hash = GENESIS_HASH if start_seq == 0 else None
        if start_seq > 0:
            prior = conn.execute(
                text("SELECT entry_hash FROM dms_audit_ledger WHERE seq = :seq"),
                {"seq": start_seq - 1},
            ).mappings().first()
            prev_hash = prior["entry_hash"] if prior else GENESIS_HASH
        if not rows:
            if start_seq > 0:
                return VerifyResult(ok=False, broken_at=start_seq)
            return VerifyResult(ok=True)
        first = int(rows[0]["seq"])
        if start_seq == 0 and first != 0:
            return VerifyResult(ok=False, broken_at=first)
        expected_seq = start_seq if start_seq > 0 else first
        for row in rows:
            seq = int(row["seq"])
            if seq != expected_seq:
                return VerifyResult(ok=False, broken_at=seq)
            payload = json.loads(row["payload"])
            expected = compute_entry_hash(seq, prev_hash or GENESIS_HASH, payload, row["created_at"])
            if row["entry_hash"] != expected or row["prev_hash"] != (prev_hash or GENESIS_HASH):
                return VerifyResult(ok=False, broken_at=seq)
            prev_hash = row["entry_hash"]
            expected_seq = seq + 1
        return VerifyResult(ok=True)


def verify(*, db_path: Path | str | None = None, start_seq: int = 0) -> VerifyResult:
    dsn = ledger_dsn()
    if dsn:
        return _postgres_verify(start_seq=start_seq)
    return _sqlite_verify(db_path=_resolve_sqlite_path(db_path), start_seq=start_seq)


def _sqlite_list_entries(
    *,
    db_path: Path,
    from_seq: int,
    limit: int,
    event_type: str | None,
) -> list[LedgerEntry]:
    con = _connect(db_path)
    try:
        init_ledger_schema(con)
        if event_type:
            rows = con.execute(
                """
                SELECT * FROM dms_audit_ledger
                WHERE seq >= ? AND event_type = ?
                ORDER BY seq ASC LIMIT ?
                """,
                (from_seq, event_type, limit),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT * FROM dms_audit_ledger
                WHERE seq >= ? ORDER BY seq ASC LIMIT ?
                """,
                (from_seq, limit),
            ).fetchall()
        return [_row_to_entry(r) for r in rows]
    finally:
        con.close()


def _postgres_list_entries(
    *,
    from_seq: int,
    limit: int,
    event_type: str | None,
) -> list[LedgerEntry]:
    from sqlalchemy import text

    dsn = ledger_dsn()
    if not dsn:
        raise RuntimeError("Postgres list_entries requested without DMS_LEDGER_DSN")
    engine = _get_postgres_engine(dsn)
    _init_postgres_schema(engine)
    with engine.connect() as conn:
        _stamp_rls(conn)
        if event_type:
            rows = conn.execute(
                text(
                    """
                    SELECT * FROM dms_audit_ledger
                    WHERE seq >= :from_seq AND event_type = :event_type
                    ORDER BY seq ASC LIMIT :limit
                    """
                ),
                {"from_seq": from_seq, "event_type": event_type, "limit": limit},
            ).mappings().all()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT * FROM dms_audit_ledger
                    WHERE seq >= :from_seq ORDER BY seq ASC LIMIT :limit
                    """
                ),
                {"from_seq": from_seq, "limit": limit},
            ).mappings().all()
        return [_row_to_entry(dict(r)) for r in rows]


def list_entries(
    *,
    db_path: Path | str | None = None,
    from_seq: int = 0,
    limit: int = 100,
    event_type: str | None = None,
) -> list[LedgerEntry]:
    dsn = ledger_dsn()
    if dsn:
        return _postgres_list_entries(from_seq=from_seq, limit=limit, event_type=event_type)
    return _sqlite_list_entries(
        db_path=_resolve_sqlite_path(db_path),
        from_seq=from_seq,
        limit=limit,
        event_type=event_type,
    )
