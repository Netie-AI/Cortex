"""Submit / ask run telemetry — in-process ring (C4-min) + durable query_run (C8).

Every ``record_run`` lands in the ring for fast recent inspection and in
``data/engine/query_run.db`` (SQLite WAL) for plausibility / L0 promotion (C10).
Telemetry must never break the run it describes.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from CortexOS.paths import data_path

_LOG = logging.getLogger("cortex.execution.submit")
_LOCK = threading.Lock()
_RECENT: deque[dict[str, Any]] = deque(maxlen=500)

DB_PATH = data_path("engine", "query_run.db")
_initialized = False


@dataclass(slots=True)
class RunRecord:
    run_id: str
    session_id: str | None
    pool_id: str | None
    kind: str
    status: str
    queue_ms: float | None
    exec_ms: float | None
    row_count: int | None
    issuer_kid: str | None
    error: str | None
    recorded_at: str


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    global _initialized
    with _LOCK, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS query_run (
              run_id TEXT PRIMARY KEY,
              session_id TEXT,
              pool_id TEXT,
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              queue_ms REAL,
              exec_ms REAL,
              row_count INTEGER,
              issuer_kid TEXT,
              error TEXT,
              recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_query_run_session
              ON query_run(session_id);
            CREATE INDEX IF NOT EXISTS idx_query_run_recorded
              ON query_run(recorded_at);
            """
        )
    _initialized = True


def _ensure_init() -> None:
    if not _initialized:
        init()


def _persist(payload: dict[str, Any]) -> None:
    _ensure_init()
    try:
        with _LOCK, _conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO query_run (
                  run_id, session_id, pool_id, kind, status,
                  queue_ms, exec_ms, row_count, issuer_kid, error, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["run_id"],
                    payload.get("session_id"),
                    payload.get("pool_id"),
                    payload["kind"],
                    payload["status"],
                    payload.get("queue_ms"),
                    payload.get("exec_ms"),
                    payload.get("row_count"),
                    payload.get("issuer_kid"),
                    payload.get("error"),
                    payload["recorded_at"],
                ),
            )
    except Exception:  # noqa: BLE001 — telemetry must never break the run
        pass


def new_run_id() -> str:
    return str(uuid.uuid4())


def record_run(
    *,
    run_id: str,
    kind: str,
    status: str,
    session_id: str | None = None,
    pool_id: str | None = None,
    queue_ms: float | None = None,
    exec_ms: float | None = None,
    row_count: int | None = None,
    issuer_kid: str | None = None,
    error: str | None = None,
) -> RunRecord:
    rec = RunRecord(
        run_id=run_id,
        session_id=session_id,
        pool_id=pool_id,
        kind=kind,
        status=status,
        queue_ms=queue_ms,
        exec_ms=exec_ms,
        row_count=row_count,
        issuer_kid=issuer_kid,
        error=error,
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )
    payload = asdict(rec)
    with _LOCK:
        _RECENT.appendleft(payload)
    _persist(payload)
    _LOG.info(
        "submit_run run_id=%s kind=%s status=%s session_id=%s pool_id=%s "
        "queue_ms=%s exec_ms=%s row_count=%s issuer_kid=%s error=%s",
        run_id,
        kind,
        status,
        session_id,
        pool_id,
        queue_ms,
        exec_ms,
        row_count,
        issuer_kid,
        error,
    )
    return rec


def get_run(run_id: str) -> dict[str, Any] | None:
    """Fetch one durable row by id (survives process restart)."""
    _ensure_init()
    with _LOCK, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM query_run WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def recent_runs(limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        return list(_RECENT)[: max(0, limit)]


def clear_runs_for_tests() -> None:
    global _initialized
    with _LOCK:
        _RECENT.clear()
    _ensure_init()
    with _LOCK, _conn() as conn:
        conn.execute("DELETE FROM query_run")


__all__ = [
    "DB_PATH",
    "RunRecord",
    "clear_runs_for_tests",
    "get_run",
    "init",
    "new_run_id",
    "recent_runs",
    "record_run",
]
