"""SQLite chat store: threads + messages with F1 ledger on every write."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packs.dms.audit.ledger import append as ledger_append
from packs.dms.audit.ledger import default_db_path, init_ledger_schema


@dataclass(frozen=True, slots=True)
class Thread:
    id: str
    external_ref: str | None
    customer_label: str | None
    status: str
    tenant_id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    thread_id: str
    direction: str
    sender: str
    body: str
    lang: str | None
    created_at: str


def _resolve_db_path(db_path: Path | str | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    return default_db_path()


def _connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_chat_schema(con: sqlite3.Connection) -> None:
    init_ledger_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS dms_threads (
            id TEXT PRIMARY KEY,
            external_ref TEXT,
            customer_label TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            tenant_id TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dms_messages (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL REFERENCES dms_threads(id),
            direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
            sender TEXT NOT NULL,
            body TEXT NOT NULL,
            lang TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dms_messages_thread
            ON dms_messages(thread_id, created_at);
        """
    )
    con.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _thread_from_row(row: sqlite3.Row) -> Thread:
    return Thread(
        id=row["id"],
        external_ref=row["external_ref"],
        customer_label=row["customer_label"],
        status=row["status"],
        tenant_id=row["tenant_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _message_from_row(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        thread_id=row["thread_id"],
        direction=row["direction"],
        sender=row["sender"],
        body=row["body"],
        lang=row["lang"],
        created_at=row["created_at"],
    )


def _thread_to_dict(thread: Thread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "external_ref": thread.external_ref,
        "customer_label": thread.customer_label,
        "status": thread.status,
        "tenant_id": thread.tenant_id,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
    }


def _message_to_dict(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "direction": message.direction,
        "sender": message.sender,
        "body": message.body,
        "lang": message.lang,
        "created_at": message.created_at,
    }


def create_thread(
    *,
    external_ref: str | None = None,
    customer_label: str | None = None,
    actor: str = "system",
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    path = _resolve_db_path(db_path)
    thread_id = str(uuid.uuid4())
    now = _now_iso()
    con = _connect(path)
    try:
        init_chat_schema(con)
        con.execute(
            """
            INSERT INTO dms_threads
            (id, external_ref, customer_label, status, tenant_id, created_at, updated_at)
            VALUES (?, ?, ?, 'open', ?, ?, ?)
            """,
            (thread_id, external_ref, customer_label, tenant_id, now, now),
        )
        con.commit()
        thread = _thread_from_row(
            con.execute("SELECT * FROM dms_threads WHERE id = ?", (thread_id,)).fetchone()
        )
    finally:
        con.close()

    ledger_entry = ledger_append(
        actor,
        "thread.created",
        {
            "thread_id": thread_id,
            "external_ref": external_ref,
            "customer_label": customer_label,
            "tenant_id": tenant_id,
        },
        db_path=path,
    )
    return {
        "thread": _thread_to_dict(thread),
        "ledger_seq": ledger_entry.seq,
    }


def append_message(
    *,
    thread_id: str,
    sender: str,
    body: str,
    direction: str = "inbound",
    lang: str | None = None,
    actor: str = "system",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    if direction not in ("inbound", "outbound"):
        raise ValueError("direction must be inbound or outbound")

    path = _resolve_db_path(db_path)
    message_id = str(uuid.uuid4())
    now = _now_iso()
    con = _connect(path)
    try:
        init_chat_schema(con)
        row = con.execute("SELECT id FROM dms_threads WHERE id = ?", (thread_id,)).fetchone()
        if row is None:
            raise ValueError(f"thread not found: {thread_id}")

        con.execute(
            """
            INSERT INTO dms_messages
            (id, thread_id, direction, sender, body, lang, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, thread_id, direction, sender, body, lang, now),
        )
        con.execute(
            "UPDATE dms_threads SET updated_at = ? WHERE id = ?",
            (now, thread_id),
        )
        con.commit()
        message = _message_from_row(
            con.execute("SELECT * FROM dms_messages WHERE id = ?", (message_id,)).fetchone()
        )
    finally:
        con.close()

    ledger_seq = None
    if direction == "inbound":
        ledger_entry = ledger_append(
            actor,
            "message.inbound",
            {
                "thread_id": thread_id,
                "message_id": message_id,
                "sender": sender,
                "body_len": len(body),
            },
            db_path=path,
        )
        ledger_seq = ledger_entry.seq

    return {
        "message": _message_to_dict(message),
        "ledger_seq": ledger_seq,
    }


def list_threads(
    *,
    status: str | None = None,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_chat_schema(con)
        if status:
            rows = con.execute(
                """
                SELECT * FROM dms_threads
                WHERE tenant_id = ? AND status = ?
                ORDER BY updated_at DESC
                """,
                (tenant_id, status),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT * FROM dms_threads
                WHERE tenant_id = ?
                ORDER BY updated_at DESC
                """,
                (tenant_id,),
            ).fetchall()
        return [_thread_to_dict(_thread_from_row(r)) for r in rows]
    finally:
        con.close()


def list_messages(
    thread_id: str,
    *,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_chat_schema(con)
        row = con.execute("SELECT id FROM dms_threads WHERE id = ?", (thread_id,)).fetchone()
        if row is None:
            raise ValueError(f"thread not found: {thread_id}")
        rows = con.execute(
            """
            SELECT * FROM dms_messages
            WHERE thread_id = ?
            ORDER BY created_at ASC
            """,
            (thread_id,),
        ).fetchall()
        return [_message_to_dict(_message_from_row(r)) for r in rows]
    finally:
        con.close()
