"""Content-addressed step journal for resumable workflow runs.

distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md
  Workflow journal with content-addressed v2:<sha256> step keys enabling cached resume.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from CortexOS.paths import data_path

DEFAULT_DB = data_path("engine", "step_journal.db")
_lock = threading.Lock()


def step_key(prompt: str, opts: dict[str, Any] | None = None) -> str:
    payload = {"prompt": prompt or "", "opts": opts or {}}
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"v2:{digest}"


def _conn(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init(db_path: Path | None = None) -> None:
    with _lock, _conn(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS step_journal (
              run_id TEXT NOT NULL,
              step_key TEXT NOT NULL,
              node_id TEXT,
              result_json TEXT NOT NULL,
              created_at REAL NOT NULL,
              PRIMARY KEY (run_id, step_key)
            )
            """
        )


def get_cached(
    run_id: str,
    key: str,
    *,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    init(db_path)
    with _lock, _conn(db_path) as conn:
        row = conn.execute(
            "SELECT result_json FROM step_journal WHERE run_id=? AND step_key=?",
            (run_id, key),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["result_json"])
    except json.JSONDecodeError:
        return None


def put_cached(
    run_id: str,
    key: str,
    result: dict[str, Any],
    *,
    node_id: str = "",
    db_path: Path | None = None,
) -> None:
    init(db_path)
    with _lock, _conn(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO step_journal(run_id, step_key, node_id, result_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, key, node_id, json.dumps(result, default=str), time.time()),
        )
