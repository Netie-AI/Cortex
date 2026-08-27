"""Crew persistence — spaces, agents, messages, runs, settings.

One SQLite file under ``data/engine/``. Same conventions as the other engine
stores: module-level ``DB_PATH`` so tests monkeypatch it, WAL, additive
migrations, no ORM.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from CortexOS.paths import data_path

DB_PATH: Path = data_path("engine", "crew.db")

MAX_HOP = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    name TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '🤖',
    color TEXT NOT NULL DEFAULT '#5eead4',
    system_prompt TEXT NOT NULL DEFAULT '',
    model TEXT,
    computer_enabled INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    space_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    from_kind TEXT NOT NULL,
    from_id TEXT,
    to_kind TEXT NOT NULL,
    to_id TEXT,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    hop INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages (channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_run ON messages (run_id);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    root_agent_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    started_at REAL NOT NULL,
    settled_at REAL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    if "meta" in d:
        try:
            d["meta"] = json.loads(d["meta"])
        except (TypeError, ValueError):
            d["meta"] = {}
    return d


# -- spaces -----------------------------------------------------------------


def create_space(name: str) -> dict[str, Any]:
    with _connect() as conn:
        sid = _new_id("sp")
        conn.execute(
            "INSERT INTO spaces (id, name, created_at) VALUES (?, ?, ?)",
            (sid, name.strip() or "Space", time.time()),
        )
        return {"id": sid, "name": name.strip() or "Space"}


def list_spaces() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM spaces ORDER BY created_at").fetchall()
        return [_row_to_dict(r) for r in rows]


def rename_space(space_id: str, name: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE spaces SET name = ? WHERE id = ?", (name.strip(), space_id)
        )
        return cur.rowcount > 0


# -- agents -----------------------------------------------------------------

_AGENT_FIELDS = {
    "name",
    "emoji",
    "color",
    "system_prompt",
    "model",
    "computer_enabled",
    "paused",
    "pinned",
    "notes",
}


def create_agent(space_id: str, draft: dict[str, Any]) -> dict[str, Any]:
    with _connect() as conn:
        aid = _new_id("ag")
        now = time.time()
        conn.execute(
            "INSERT INTO agents (id, space_id, name, emoji, color, system_prompt,"
            " model, computer_enabled, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                aid,
                space_id,
                str(draft.get("name") or "Agent").strip()[:60],
                str(draft.get("emoji") or "🤖")[:8],
                str(draft.get("color") or "#5eead4")[:16],
                str(draft.get("system_prompt") or ""),
                draft.get("model") or None,
                1 if draft.get("computer_enabled") else 0,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (aid,)).fetchone()
        return _row_to_dict(row)


def list_agents(include_deleted: bool = False) -> list[dict[str, Any]]:
    with _connect() as conn:
        q = "SELECT * FROM agents"
        if not include_deleted:
            q += " WHERE deleted = 0"
        q += " ORDER BY pinned DESC, created_at"
        return [_row_to_dict(r) for r in conn.execute(q).fetchall()]


def get_agent(agent_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return _row_to_dict(row) if row else None


def update_agent(agent_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    fields = {k: v for k, v in patch.items() if k in _AGENT_FIELDS}
    if not fields:
        return get_agent(agent_id)
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = [
        int(bool(v)) if k in ("computer_enabled", "paused", "pinned") else v
        for k, v in fields.items()
    ]
    with _connect() as conn:
        conn.execute(
            f"UPDATE agents SET {sets}, updated_at = ? WHERE id = ?",
            (*vals, time.time(), agent_id),
        )
    return get_agent(agent_id)


def delete_agent(agent_id: str) -> bool:
    """Soft delete — history stays readable, the agent leaves the rail."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE agents SET deleted = 1, updated_at = ? WHERE id = ?",
            (time.time(), agent_id),
        )
        return cur.rowcount > 0


def find_agent_by_name(space_id: str, name: str) -> dict[str, Any] | None:
    """Resolve a peer by name inside one space only — spaces are walls."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM agents WHERE space_id = ? AND deleted = 0"
            " AND lower(name) = lower(?)",
            (space_id, name.strip().lstrip("@")),
        ).fetchone()
        return _row_to_dict(row) if row else None


# -- messages ---------------------------------------------------------------


def append_message(
    *,
    space_id: str,
    channel_id: str,
    from_kind: str,
    from_id: str | None,
    to_kind: str,
    to_id: str | None,
    kind: str,
    content: str,
    run_id: str | None = None,
    meta: dict[str, Any] | None = None,
    hop: int = 0,
) -> dict[str, Any]:
    with _connect() as conn:
        mid = _new_id("msg")
        now = time.time()
        conn.execute(
            "INSERT INTO messages (id, run_id, space_id, channel_id, from_kind,"
            " from_id, to_kind, to_id, kind, content, meta, hop, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                mid,
                run_id,
                space_id,
                channel_id,
                from_kind,
                from_id,
                to_kind,
                to_id,
                kind,
                content,
                json.dumps(meta or {}),
                hop,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
        return _row_to_dict(row)


def channel_messages(channel_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE channel_id = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (channel_id, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in reversed(rows)]


def agent_transcript(agent_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """What this agent's conversation shows: its channel, plus what it sent
    to peers (those rows live under the receiver's channel)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE channel_id = ?"
            " OR (from_id = ? AND kind = 'a2a')"
            " ORDER BY created_at DESC LIMIT ?",
            (agent_id, agent_id, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in reversed(rows)]


def flow_messages(space_id: str | None = None, limit: int = 400) -> list[dict[str, Any]]:
    """The activity feed: every message in newest-run-first order."""
    with _connect() as conn:
        if space_id:
            rows = conn.execute(
                "SELECT * FROM messages WHERE space_id = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (space_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in reversed(rows)]


# -- runs -------------------------------------------------------------------


def create_run(space_id: str, root_agent_id: str) -> str:
    with _connect() as conn:
        rid = _new_id("run")
        conn.execute(
            "INSERT INTO runs (id, space_id, root_agent_id, started_at) VALUES (?, ?, ?, ?)",
            (rid, space_id, root_agent_id, time.time()),
        )
        return rid


def add_run_usage(run_id: str, prompt_tokens: int, completion_tokens: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE runs SET prompt_tokens = prompt_tokens + ?,"
            " completion_tokens = completion_tokens + ? WHERE id = ?",
            (int(prompt_tokens), int(completion_tokens), run_id),
        )


def settle_run(run_id: str, status: str = "settled") -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, settled_at = ? WHERE id = ? AND status = 'running'",
            (status, time.time(), run_id),
        )


def get_run(run_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def usage_summary() -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS runs, COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,"
            " COALESCE(SUM(completion_tokens),0) AS completion_tokens FROM runs"
        ).fetchone()
        return dict(row)


# -- settings ---------------------------------------------------------------


def get_setting(key: str, default: str | None = None) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
