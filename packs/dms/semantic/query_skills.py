"""Reusable NL→SQL query skills — cheap similarity over past successful answers.

No LLM on the hot path: bag-of-words hash embeddings (same as F6 skill capture)
match a question to a stored metric_id+params (or certified SQL), then the
caller recompiles through Q1 + sql_guardrail.

Off unless ``CORTEX_QUERY_SKILL=1`` (#340). Even when on, the store is scoped
per Space and never touched during a scored round: a read or write with no
Space, or with a ``scored_pack_id`` / ``CORTEX_SCORED_ROUND``, is a no-op.
Space and scored pack come from explicit arguments or the ask scope
(``CortexOS.dms.ask_scope``) that ``POST /v1/contract/ask`` opens.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from CortexOS.dms.ask_scope import current_ask_scope
from packs.dms.skills.capture import cosine_similarity, normalize_trigger, text_embedding

# High bar: avoid false skill hits that would skip a better metric route.
DEFAULT_THRESHOLD = 0.72

_log = logging.getLogger(__name__)

ENABLED_ENV = "CORTEX_QUERY_SKILL"
# Pre-#340 tables keep a global UNIQUE(trigger_text); a capture that hits it abstains.
CROSS_SPACE_COLLISION = "QUERY_SKILL_CROSS_SPACE_COLLISION"
LEGACY_ROW_COLLISION = "QUERY_SKILL_LEGACY_ROW_COLLISION"
# Same knob C-MEM honours (CortexOS.memory.space_memory.SCORED_ROUND_ENV).
SCORED_ROUND_ENV = "CORTEX_SCORED_ROUND"
_TRUTHY = ("1", "true", "yes", "on")


def query_skill_enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "0").strip().lower() in _TRUTHY


def capture_enabled() -> bool:
    if not query_skill_enabled():
        return False
    raw = os.environ.get("DMS_QUERY_SKILL_CAPTURE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _skill_space(space_id: str | None, scored_pack_id: str | None) -> str | None:
    """The Space this read/write is scoped to, or None when the store is off-limits.

    The engine calls ``find``/``capture`` with the question only; the Space and
    scored pack then come from the ask scope ``POST /v1/contract/ask`` opens.
    """
    if not query_skill_enabled():
        return None
    scope = current_ask_scope()
    if (scored_pack_id or "").strip() or scope.scored_pack_id:
        return None
    if os.environ.get(SCORED_ROUND_ENV, "").strip().lower() in _TRUTHY:
        return None
    space = space_id if space_id is not None else scope.space_id
    return (space or "").strip() or None


# Resolved once: Path.resolve() is a filesystem syscall, and this ran on every
# query (twice — capture and find). The repo root cannot move at runtime.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DIRS_MADE: set[str] = set()


def _sqlite_path() -> str:
    env = os.environ.get("DMS_OPS_DB") or os.environ.get("SQLITE_DB_PATH")
    if env:
        return env
    return str(_REPO_ROOT / "packs" / "data" / "dms_ops.db")


def _connect() -> sqlite3.Connection:
    path = Path(_sqlite_path())
    parent = str(path.parent)
    if parent not in _DIRS_MADE:
        # mkdir is a syscall too; the directory only needs creating once.
        path.parent.mkdir(parents=True, exist_ok=True)
        _DIRS_MADE.add(parent)
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


_SCHEMA_READY: set[str] = set()
_SCHEMA_LOCK = threading.Lock()


def ensure_schema(con: sqlite3.Connection) -> None:
    """Create the table once per process per DB path.

    ``init_query_skills_schema`` ran an ``executescript`` (CREATE TABLE IF NOT
    EXISTS + CREATE INDEX, plus a commit) on EVERY captured answer and every
    lookup — DDL that can only matter once, on the hot path of every question.
    """
    path = _sqlite_path()
    if path in _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if path in _SCHEMA_READY:
            return
        init_query_skills_schema(con)
        _SCHEMA_READY.add(path)


def reset_schema_cache() -> None:
    """Test hook: forget which DB paths have been initialised (a test that
    points DMS_OPS_DB at a fresh tmp file must re-create the table)."""
    with _SCHEMA_LOCK:
        _SCHEMA_READY.clear()
    _DIRS_MADE.clear()


def init_query_skills_schema(con: sqlite3.Connection | None = None) -> None:
    own = con is None
    if own:
        con = _connect()
    assert con is not None
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS dms_query_skills (
            id TEXT PRIMARY KEY,
            trigger_text TEXT NOT NULL,
            embedding TEXT NOT NULL DEFAULT '[]',
            metric_id TEXT,
            params_json TEXT NOT NULL DEFAULT '{}',
            sql_template TEXT,
            layer TEXT NOT NULL DEFAULT 'governed_metric',
            support_count INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            last_used_at TEXT,
            created_at TEXT NOT NULL,
            space_id TEXT,
            UNIQUE (space_id, trigger_text)
        );
        CREATE INDEX IF NOT EXISTS idx_dms_query_skills_active
            ON dms_query_skills(active);
        """
    )
    # Additive only. A table created before #340 keeps its global
    # UNIQUE(trigger_text) (dropping it needs a table rebuild) and its rows keep
    # space_id NULL, which is never read. capture() turns a collision with that
    # constraint into a stamped abstain.
    cols = {r[1] for r in con.execute("PRAGMA table_info(dms_query_skills)")}
    if "space_id" not in cols:
        con.execute("ALTER TABLE dms_query_skills ADD COLUMN space_id TEXT")
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_dms_query_skills_space_trigger "
        "ON dms_query_skills(space_id, trigger_text)"
    )
    con.commit()
    if own:
        con.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find(
    question: str,
    *,
    space_id: str | None = None,
    scored_pack_id: str | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any] | None:
    """Return this Space's best active skill above threshold, or None."""
    space = _skill_space(space_id, scored_pack_id)
    if space is None:
        return None
    text = normalize_trigger(question)
    if not text:
        return None
    q_emb = text_embedding(text)
    con = _connect()
    try:
        ensure_schema(con)
        rows = con.execute(
            "SELECT * FROM dms_query_skills WHERE active = 1 AND space_id = ?",
            (space,),
        ).fetchall()
        best: dict[str, Any] | None = None
        best_score = 0.0
        for row in rows:
            pat = normalize_trigger(row["trigger_text"] or "")
            if not pat:
                continue
            # Exact normalized match only for a perfect score. Substring
            # containment used to replay a longer stored question (with
            # exclusions / windows) onto a shorter unrelated top-N ask.
            if pat == text:
                score = 1.0
            else:
                try:
                    emb = json.loads(row["embedding"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    emb = []
                cos = cosine_similarity(q_emb, emb) if emb else 0.0
                pt, tt = set(pat.split()), set(text.split())
                jaccard = len(pt & tt) / len(pt | tt) if pt | tt else 0.0
                score = max(cos, jaccard)
            if score > best_score:
                best_score = score
                try:
                    params = json.loads(row["params_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    params = {}
                best = {
                    "id": row["id"],
                    "trigger_text": row["trigger_text"],
                    "metric_id": row["metric_id"],
                    "params": params if isinstance(params, dict) else {},
                    "sql_template": row["sql_template"],
                    "layer": row["layer"] or "query_skill",
                    "score": score,
                }
        if best is None or best_score < threshold:
            return None
        con.execute(
            "UPDATE dms_query_skills SET support_count = support_count + 1, "
            "last_used_at = ? WHERE id = ? AND space_id = ?",
            (_now(), best["id"], space),
        )
        con.commit()
        return best
    finally:
        con.close()


def capture(
    question: str,
    *,
    metric_id: str | None,
    params: dict[str, Any] | None = None,
    sql: str | None = None,
    layer: str = "governed_metric",
    space_id: str | None = None,
    scored_pack_id: str | None = None,
) -> dict[str, Any] | None:
    """Persist a successful answer as a reusable skill (idempotent per Space + trigger)."""
    if not capture_enabled():
        return None
    space = _skill_space(space_id, scored_pack_id)
    if space is None:
        return None
    text = normalize_trigger(question)
    if not text or (not metric_id and not sql):
        return None
    emb = text_embedding(text)
    now = _now()
    skill_id = str(uuid.uuid4())
    con = _connect()
    try:
        ensure_schema(con)
        existing = con.execute(
            "SELECT id FROM dms_query_skills WHERE space_id = ? AND trigger_text = ?",
            (space, text),
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE dms_query_skills SET support_count = support_count + 1, "
                "last_used_at = ?, metric_id = COALESCE(?, metric_id), "
                "params_json = ?, sql_template = COALESCE(?, sql_template), "
                "layer = ?, embedding = ? WHERE id = ? AND space_id = ?",
                (
                    now,
                    metric_id,
                    json.dumps(params or {}),
                    sql,
                    layer,
                    json.dumps(emb),
                    existing["id"],
                    space,
                ),
            )
            con.commit()
            return {"id": existing["id"], "updated": True}
        try:
            con.execute(
                "INSERT INTO dms_query_skills "
                "(id, trigger_text, embedding, metric_id, params_json, sql_template, "
                "layer, support_count, active, last_used_at, created_at, space_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)",
                (
                    skill_id,
                    text,
                    json.dumps(emb),
                    metric_id,
                    json.dumps(params or {}),
                    sql,
                    layer,
                    now,
                    now,
                    space,
                ),
            )
        except sqlite3.IntegrityError:
            con.rollback()
            holder = con.execute(
                "SELECT space_id FROM dms_query_skills WHERE trigger_text = ? "
                "AND (space_id IS NULL OR space_id != ?)",
                (text, space),
            ).fetchone()
            if holder is None:
                raise
            reason = LEGACY_ROW_COLLISION if holder["space_id"] is None else CROSS_SPACE_COLLISION
            return _abstain_stamp(space, reason)
        con.commit()
        return {"id": skill_id, "updated": False}
    finally:
        con.close()


def _abstain_stamp(space: str, reason: str) -> dict[str, Any]:
    """A capture that did not write, said out loud. Never names the other Space."""
    stamp = {
        "served_op": "abstain",
        "served_space_id": space,
        "served_reason": reason,
        "served_at": _now(),
        "written": False,
    }
    _log.warning("query_skill capture abstained in Space %r: %s", space, reason)
    return stamp


def clear_all() -> None:
    """Test helper — wipe query skills."""
    con = _connect()
    try:
        ensure_schema(con)
        con.execute("DELETE FROM dms_query_skills")
        con.commit()
    finally:
        con.close()
