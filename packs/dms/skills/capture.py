"""F6 skill capture — opt-in, internal-only, feeds F4 suggest()."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packs.dms.audit.ledger import append as ledger_append
from packs.dms.audit.ledger import default_db_path
from packs.dms.tasks.gate import init_task_events_schema

_SKILL_BOOST_WEIGHT = 0.25
_MATCH_THRESHOLD = 0.5
_EMBED_DIM = 32


def _sqlite_path() -> str:
    env = os.environ.get("DMS_OPS_DB") or os.environ.get("SQLITE_DB_PATH")
    return env if env else str(default_db_path())


def capture_enabled() -> bool:
    return os.environ.get("DMS_SKILL_CAPTURE_ENABLED", "").lower() in ("1", "true", "yes")


def set_capture_enabled(enabled: bool) -> bool:
    os.environ["DMS_SKILL_CAPTURE_ENABLED"] = "true" if enabled else "false"
    return capture_enabled()


def _connect() -> sqlite3.Connection:
    path = Path(_sqlite_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _migrate_task_events(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(dms_task_events)")}
    if "outcome" not in cols:
        con.execute("ALTER TABLE dms_task_events ADD COLUMN outcome TEXT")
    if "trigger_text" not in cols:
        con.execute("ALTER TABLE dms_task_events ADD COLUMN trigger_text TEXT")


def init_skills_schema(con: sqlite3.Connection | None = None) -> None:
    own = con is None
    if own:
        con = _connect()
    assert con is not None
    init_task_events_schema(con)
    _migrate_task_events(con)
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
            active INTEGER NOT NULL DEFAULT 1,
            last_used_at TEXT,
            created_by TEXT NOT NULL,
            consented INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE (intent, trigger_pattern)
        );
        CREATE INDEX IF NOT EXISTS idx_dms_skills_task ON dms_skills(task_id);
        """
    )
    con.commit()
    if own:
        con.close()


def normalize_trigger(text: str) -> str:
    return " ".join(text.lower().split())


def text_embedding(text: str) -> list[float]:
    """Deterministic bag-of-words hash embedding — no ML deps."""
    words = re.findall(r"\w+", text.lower())
    vec = [0.0] * _EMBED_DIM
    for word in words:
        vec[hash(word) % _EMBED_DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def match_trigger(trigger_text: str, pattern: str, embedding_json: str) -> float:
    text = normalize_trigger(trigger_text)
    pat = normalize_trigger(pattern)
    if not text or not pat:
        return 0.0
    if pat in text or text in pat:
        return 1.0
    pt = set(pat.split())
    tt = set(text.split())
    jaccard = len(pt & tt) / len(pt | tt) if pt | tt else 0.0
    try:
        emb = json.loads(embedding_json or "[]")
        if isinstance(emb, list) and emb:
            cos = cosine_similarity(text_embedding(text), emb)
            return max(jaccard, cos)
    except (json.JSONDecodeError, TypeError):
        pass
    return jaccard


def _row_to_skill(row: sqlite3.Row) -> dict[str, Any]:
    try:
        embedding = json.loads(row["embedding"] or "[]")
    except (json.JSONDecodeError, TypeError):
        embedding = []
    return {
        "id": row["id"],
        "intent": row["intent"],
        "trigger_pattern": row["trigger_pattern"],
        "embedding": embedding,
        "task_id": row["task_id"],
        "template": json.loads(row["template"] or "{}"),
        "support_count": row["support_count"],
        "success_count": row["success_count"],
        "active": bool(row["active"]),
        "last_used_at": row["last_used_at"],
        "created_by": row["created_by"],
        "consented": bool(row["consented"]),
        "created_at": row["created_at"],
    }


def list_skills(*, active_only: bool = True) -> list[dict[str, Any]]:
    con = _connect()
    try:
        init_skills_schema(con)
        if active_only:
            rows = con.execute(
                "SELECT * FROM dms_skills WHERE active = 1 ORDER BY success_count DESC"
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM dms_skills ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_skill(r) for r in rows]
    finally:
        con.close()


def load_active_skills_for_suggest() -> list[dict[str, Any]]:
    return list_skills(active_only=True)


def deactivate_skill(skill_id: str, *, actor: str = "steward") -> bool:
    con = _connect()
    try:
        init_skills_schema(con)
        cur = con.execute(
            "UPDATE dms_skills SET active = 0 WHERE id = ? AND active = 1",
            (skill_id,),
        )
        con.commit()
        if cur.rowcount:
            ledger_append(
                actor,
                "skill.deactivated",
                {"skill_id": skill_id},
                db_path=Path(_sqlite_path()),
            )
            return True
        return False
    finally:
        con.close()


def capture_from_event(
    event_id: str,
    *,
    trigger_text: str | None = None,
    actor: str = "system",
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    if not capture_enabled():
        return None

    path = Path(db_path) if db_path else Path(_sqlite_path())
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        init_skills_schema(con)
        row = con.execute(
            "SELECT * FROM dms_task_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        if row["gate_status"] != "pass" or not row["executable"]:
            return None
        if row["outcome"] != "success":
            return None

        intent = row["intent"] or row["task_id"]
        resolved_trigger = trigger_text or row["trigger_text"] or intent
        pattern = normalize_trigger(resolved_trigger)
        if not pattern:
            return None

        template = json.loads(row["filled_template"] or "{}")
        embedding = text_embedding(pattern)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        existing = con.execute(
            "SELECT id, support_count, success_count FROM dms_skills "
            "WHERE intent = ? AND trigger_pattern = ?",
            (intent, pattern),
        ).fetchone()

        if existing:
            skill_id = existing["id"]
            con.execute(
                """
                UPDATE dms_skills
                SET support_count = support_count + 1,
                    success_count = success_count + 1,
                    last_used_at = ?,
                    template = ?
                WHERE id = ?
                """,
                (now, json.dumps(template), skill_id),
            )
            action = "updated"
        else:
            skill_id = str(uuid.uuid4())
            con.execute(
                """
                INSERT INTO dms_skills
                (id, intent, trigger_pattern, embedding, task_id, template,
                 support_count, success_count, active, last_used_at,
                 created_by, consented, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 1, 1, ?, ?, 1, ?)
                """,
                (
                    skill_id,
                    intent,
                    pattern,
                    json.dumps(embedding),
                    row["task_id"],
                    json.dumps(template),
                    now,
                    actor,
                    now,
                ),
            )
            action = "created"

        con.commit()
    finally:
        con.close()

    payload = {
        "skill_id": skill_id,
        "event_id": event_id,
        "task_id": row["task_id"],
        "intent": intent,
        "trigger_pattern": pattern,
        "action": action,
    }
    ledger_append(actor, "skill.captured", payload, db_path=path)
    return payload


def complete_event(
    event_id: str,
    outcome: str,
    *,
    trigger_text: str | None = None,
    actor: str = "user",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    path = Path(db_path) if db_path else Path(_sqlite_path())
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        init_skills_schema(con)
        row = con.execute(
            "SELECT task_id FROM dms_task_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"task event not found: {event_id}")

        con.execute(
            """
            UPDATE dms_task_events
            SET outcome = ?, trigger_text = COALESCE(?, trigger_text)
            WHERE id = ?
            """,
            (outcome, trigger_text, event_id),
        )
        con.commit()
        task_id = row["task_id"]
    finally:
        con.close()

    from packs.dms.tasks.suggest import record_outcome

    record_outcome(task_id, outcome)

    captured = None
    if outcome == "success":
        captured = capture_from_event(
            event_id,
            trigger_text=trigger_text,
            actor=actor,
            db_path=path,
        )

    return {
        "ok": True,
        "event_id": event_id,
        "outcome": outcome,
        "captured": captured,
    }


def boost_candidates_from_skills(
    candidates: list[dict],
    trigger_text: str | None,
) -> list[dict]:
    if not trigger_text or not candidates:
        return candidates

    skills = load_active_skills_for_suggest()
    if not skills:
        return candidates

    for cand in candidates:
        best = 0.0
        for skill in skills:
            if skill["task_id"] != cand["task_id"]:
                continue
            emb = skill.get("embedding") or text_embedding(skill["trigger_pattern"])
            emb_json = json.dumps(emb) if isinstance(emb, list) else "[]"
            score = match_trigger(trigger_text, skill["trigger_pattern"], emb_json)
            best = max(best, score)
        if best >= _MATCH_THRESHOLD:
            cand["confidence"] = min(
                1.0,
                cand.get("confidence", 0.0) + _SKILL_BOOST_WEIGHT * best,
            )
            cand["skill_match"] = round(best, 3)

    return candidates
