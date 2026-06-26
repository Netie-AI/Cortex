"""F4 batch learning — nightly stats refresh."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from packs.dms.audit.ledger import default_db_path


def _sqlite_path() -> str:
    env = os.environ.get("DMS_OPS_DB") or os.environ.get("SQLITE_DB_PATH")
    return env if env else str(default_db_path())


def refresh_stats() -> dict:
    try:
        with sqlite3.connect(_sqlite_path()) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dms_task_stats (
                    task_id TEXT PRIMARY KEY,
                    total_shown INTEGER DEFAULT 0,
                    total_accepted INTEGER DEFAULT 0,
                    total_success INTEGER DEFAULT 0,
                    accept_rate REAL DEFAULT 0.0,
                    success_rate REAL DEFAULT 0.0,
                    last_refreshed TEXT
                )
            """)
            rows = conn.execute("""
                SELECT
                    task_id,
                    COUNT(*) as total,
                    SUM(accepted) as accepted,
                    SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as success
                FROM dms_task_choices
                GROUP BY task_id
            """).fetchall()

            ts = datetime.now(timezone.utc).isoformat()
            for row in rows:
                task_id, total, accepted, success = row
                accept_rate = round(accepted / total, 3) if total > 0 else 0.0
                success_rate = round(success / accepted, 3) if accepted > 0 else 0.0
                conn.execute("""
                    INSERT INTO dms_task_stats
                        (task_id, total_shown, total_accepted, total_success,
                         accept_rate, success_rate, last_refreshed)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        total_shown = excluded.total_shown,
                        total_accepted = excluded.total_accepted,
                        total_success = excluded.total_success,
                        accept_rate = excluded.accept_rate,
                        success_rate = excluded.success_rate,
                        last_refreshed = excluded.last_refreshed
                """, (task_id, total, accepted, success, accept_rate, success_rate, ts))
            conn.commit()
            return {"ok": True, "tasks_updated": len(rows), "refreshed_at": ts}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_stats(task_id: str | None = None) -> list[dict]:
    try:
        with sqlite3.connect(_sqlite_path()) as conn:
            if task_id:
                rows = conn.execute(
                    "SELECT * FROM dms_task_stats WHERE task_id = ?", (task_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM dms_task_stats ORDER BY accept_rate DESC"
                ).fetchall()
            cur = conn.execute("SELECT * FROM dms_task_stats LIMIT 0")
            col_names = [d[0] for d in cur.description] if cur.description else [
                "task_id", "total_shown", "total_accepted", "total_success",
                "accept_rate", "success_rate", "last_refreshed",
            ]
            return [dict(zip(col_names, r)) for r in rows]
    except Exception:
        return []
