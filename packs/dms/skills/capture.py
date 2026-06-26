"""F6 skill capture — consented, opt-in, gate-pass + success only."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packs.dms.audit.ledger import append as ledger_append
from packs.dms.audit.ledger import default_db_path
from packs.dms.skills.embed import local_embed
from packs.dms.tasks.gate import init_task_events_schema

SKILL_MATCH_THRESHOLD = 0.55


def is_capture_enabled() -> bool:
    return os.getenv("DMS_SKILL_CAPTURE_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _resolve_db(db_path: Path | str | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    return default_db_path()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_skills_schema(con: sqlite3.Connection) -> None:
    init_task_events_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS dms_skills (
            id TEXT PRIMARY KEY,
            intent TEXT NOT NULL,
            trigger_pattern TEXT NOT NULL,
            embedding TEXT NOT NULL DEFAULT '[]',
            task_id TEXT NOT NULL,
            template TEXT NOT NULL DEFAULT '{}',
            support_count INTEGER NOT NULL DEFAULT 1,
            success_count INTEGER NOT NULL DEFAULT 1,
            last_used_at TEXT,
            created_by TEXT NOT NULL,
            consented INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE(intent, trigger_pattern)
        );
        CREATE INDEX IF NOT EXISTS idx_dms_skills_task ON dms_skills(task_id);
        CREATE INDEX IF NOT EXISTS idx_dms_skills_active ON dms_skills(active, intent);
        """
    )
    con.commit()


def normalize_trigger(text: str) -> str:
    return " ".join((text or "").lower().split())


def capture_from_event(
    event_id: str,
    *,
    trigger_text: str,
    outcome: str,
    actor: str = "system",
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """
    Upsert a skill when capture is enabled, gate passed, and outcome is success.
    Returns capture summary or None when capture does not run.
    """
    if not is_capture_enabled():
        return None
    if outcome != "success":
        return None
    if not trigger_text.strip():
        return None

    path = _resolve_db(db_path)
    con = _connect(path)
    try:
        init_skills_schema(con)
        row = con.execute(
            """
            SELECT task_id, intent, filled_template, gate_status, executable
            FROM dms_task_events WHERE id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        if row["gate_status"] != "pass" or not row["executable"]:
            return None

        intent = row["intent"] or ""
        task_id = row["task_id"]
        template = json.loads(row["filled_template"] or "{}")
        trigger_pattern = normalize_trigger(trigger_text)
        embedding_json = json.dumps(local_embed(trigger_text))
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        existing = con.execute(
            """
            SELECT id FROM dms_skills
            WHERE intent = ? AND trigger_pattern = ?
            """,
            (intent, trigger_pattern),
        ).fetchone()

        if existing:
            skill_id = existing["id"]
            con.execute(
                """
                UPDATE dms_skills
                SET support_count = support_count + 1,
                    success_count = success_count + 1,
                    last_used_at = ?,
                    template = ?,
                    embedding = ?,
                    active = 1,
                    consented = 1
                WHERE id = ?
                """,
                (now, json.dumps(template), embedding_json, skill_id),
            )
            created = False
        else:
            skill_id = str(uuid.uuid4())
            con.execute(
                """
                INSERT INTO dms_skills
                (id, intent, trigger_pattern, embedding, task_id, template,
                 support_count, success_count, last_used_at, created_by,
                 consented, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?, 1, 1, ?)
                """,
                (
                    skill_id,
                    intent,
                    trigger_pattern,
                    embedding_json,
                    task_id,
                    json.dumps(template),
                    now,
                    actor,
                    now,
                ),
            )
            created = True
        con.commit()
    finally:
        con.close()

    ledger_append(
        actor,
        "skill.captured",
        {
            "skill_id": skill_id,
            "event_id": event_id,
            "task_id": task_id,
            "intent": intent,
            "trigger_pattern": trigger_pattern,
            "created": created,
        },
        db_path=path,
    )
    return {
        "skill_id": skill_id,
        "task_id": task_id,
        "intent": intent,
        "created": created,
    }


def list_skills(*, active_only: bool = False, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    path = _resolve_db(db_path)
    con = _connect(path)
    try:
        init_skills_schema(con)
        query = "SELECT * FROM dms_skills"
        if active_only:
            query += " WHERE active = 1 AND consented = 1"
        query += " ORDER BY success_count DESC, support_count DESC"
        rows = con.execute(query).fetchall()
        return [_row_to_skill(r) for r in rows]
    finally:
        con.close()


def deactivate_skill(skill_id: str, *, actor: str = "steward", db_path: Path | str | None = None) -> bool:
    path = _resolve_db(db_path)
    con = _connect(path)
    try:
        init_skills_schema(con)
        cur = con.execute(
            "UPDATE dms_skills SET active = 0 WHERE id = ?",
            (skill_id,),
        )
        con.commit()
        updated = cur.rowcount > 0
    finally:
        con.close()

    if updated:
        ledger_append(
            actor,
            "skill.deactivated",
            {"skill_id": skill_id},
            db_path=path,
        )
    return updated


def load_active_skills(db_path: Path | str | None = None) -> list[dict[str, Any]]:
    return list_skills(active_only=True, db_path=db_path)


def _row_to_skill(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "intent": row["intent"],
        "trigger_pattern": row["trigger_pattern"],
        "task_id": row["task_id"],
        "template": json.loads(row["template"] or "{}"),
        "support_count": row["support_count"],
        "success_count": row["success_count"],
        "last_used_at": row["last_used_at"],
        "created_by": row["created_by"],
        "consented": bool(row["consented"]),
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "embedding": row["embedding"],
    }
