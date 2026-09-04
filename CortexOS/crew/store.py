"""SQLite store for crew state - spaces, agents, messages, runs, confirms.

Single local operator, so one connection guarded by a lock is enough; WAL
keeps the UI's reads from blocking a writing run. All writes go through this
module so message ``seq`` stays a per-space total order the UI can page on.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spaces (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    ord INTEGER NOT NULL,
    model TEXT,
    system_prompt TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    name TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    role_prompt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'idle',
    spawned_by TEXT,
    allow_tools TEXT NOT NULL DEFAULT '',
    deny_tools TEXT NOT NULL DEFAULT '',
    capability TEXT NOT NULL DEFAULT '',
    verify INTEGER NOT NULL DEFAULT 0,
    verify_criteria TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    skills TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (space_id, name)
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    agent_id TEXT,
    to_agent_id TEXT,
    content TEXT NOT NULL,
    meta TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (space_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_messages_space_seq ON messages (space_id, seq);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    stats TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS confirms (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    run_id TEXT,
    agent_id TEXT,
    tool TEXT NOT NULL,
    args TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
"""


def _dumps(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=True)
    except (TypeError, ValueError):
        return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _loads(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


class CrewStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._migrate_agents()
            self._db.commit()

    def _migrate_agents(self) -> None:
        rows = self._db.execute("PRAGMA table_info(agents)").fetchall()
        cols = {str(r[1]) for r in rows}
        extras = (
            ("allow_tools", "TEXT NOT NULL DEFAULT ''"),
            ("deny_tools", "TEXT NOT NULL DEFAULT ''"),
            ("capability", "TEXT NOT NULL DEFAULT ''"),
            ("verify", "INTEGER NOT NULL DEFAULT 0"),
            ("verify_criteria", "TEXT NOT NULL DEFAULT ''"),
            ("model", "TEXT NOT NULL DEFAULT ''"),
            ("skills", "TEXT NOT NULL DEFAULT ''"),
        )
        for name, ddl in extras:
            if name not in cols:
                self._db.execute(f"ALTER TABLE agents ADD COLUMN {name} {ddl}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- spaces ------------------------------------------------------------

    def create_space(self, title: str = "General") -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(MAX(ord), 0) + 1 FROM spaces").fetchone()
            space_id = _new_id()
            self._db.execute(
                "INSERT INTO spaces (id, title, ord, created_at) VALUES (?, ?, ?, ?)",
                (space_id, title, int(row[0]), _now()),
            )
            self._db.commit()
        space = self.get_space(space_id)
        assert space is not None
        return space

    def get_space(self, space_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM spaces WHERE id = ?", (space_id,)).fetchone()
        return dict(row) if row else None

    def list_spaces(self, include_archived: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM spaces"
        if not include_archived:
            q += " WHERE archived = 0"
        q += " ORDER BY ord ASC"
        with self._lock:
            rows = self._db.execute(q).fetchall()
        return [dict(r) for r in rows]

    def update_space(self, space_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {k: v for k, v in fields.items() if k in {"title", "ord", "model", "system_prompt"}}
        if allowed:
            sets = ", ".join(f"{k} = ?" for k in allowed)
            with self._lock:
                self._db.execute(
                    f"UPDATE spaces SET {sets} WHERE id = ?", (*allowed.values(), space_id)
                )
                self._db.commit()
        return self.get_space(space_id)

    def archive_space(self, space_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE spaces SET archived = 1 WHERE id = ?", (space_id,))
            self._db.commit()

    # -- agents ------------------------------------------------------------

    def upsert_agent(
        self,
        space_id: str,
        name: str,
        *,
        role_prompt: str = "",
        icon: str = "",
        color: str = "",
        spawned_by: str | None = None,
        allow_tools: Any = "",
        deny_tools: Any = "",
        capability: str = "",
        verify: bool = False,
        verify_criteria: Any = "",
        model: str = "",
        skills: Any = "",
    ) -> dict[str, Any]:
        allow_s = _dumps(allow_tools)
        deny_s = _dumps(deny_tools)
        crit_s = _dumps(verify_criteria)
        skills_s = _dumps(skills)
        model_s = (model or "").strip()
        if "grok" in model_s.lower() and "fast" in model_s.lower():
            model_s = "openai/grok-4.6"
        verify_i = 1 if verify else 0
        existing = self.get_agent_by_name(space_id, name)
        if existing is not None:
            patch: dict[str, Any] = {}
            if role_prompt and role_prompt != existing.get("role_prompt"):
                patch["role_prompt"] = role_prompt
            if allow_s and allow_s != (existing.get("allow_tools") or ""):
                patch["allow_tools"] = allow_s
            if deny_s and deny_s != (existing.get("deny_tools") or ""):
                patch["deny_tools"] = deny_s
            if capability and capability != (existing.get("capability") or ""):
                patch["capability"] = capability
            if verify_i and int(existing.get("verify") or 0) != verify_i:
                patch["verify"] = verify_i
            if crit_s and crit_s != (existing.get("verify_criteria") or ""):
                patch["verify_criteria"] = crit_s
            if model_s and model_s != (existing.get("model") or ""):
                patch["model"] = model_s
            if skills_s and skills_s != (existing.get("skills") or ""):
                patch["skills"] = skills_s
            if patch:
                sets = ", ".join(f"{k} = ?" for k in patch)
                with self._lock:
                    self._db.execute(
                        f"UPDATE agents SET {sets} WHERE id = ?",
                        (*patch.values(), existing["id"]),
                    )
                    self._db.commit()
                existing.update(patch)
            return existing
        agent_id = _new_id()
        with self._lock:
            self._db.execute(
                "INSERT INTO agents (id, space_id, name, icon, color, role_prompt, spawned_by,"
                " allow_tools, deny_tools, capability, verify, verify_criteria, model, skills,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_id,
                    space_id,
                    name,
                    icon,
                    color,
                    role_prompt,
                    spawned_by,
                    allow_s,
                    deny_s,
                    capability,
                    verify_i,
                    crit_s,
                    model_s,
                    skills_s,
                    _now(),
                ),
            )
            self._db.commit()
        agent = self.get_agent(agent_id)
        assert agent is not None
        return agent

    def rename_agent(self, space_id: str, old: str, new: str) -> dict[str, Any] | None:
        target = (new or "").strip()
        source = (old or "").strip()
        if not source or not target:
            return None
        if self.get_agent_by_name(space_id, target) is not None:
            return None
        row = self.get_agent_by_name(space_id, source)
        if row is None:
            return None
        with self._lock:
            self._db.execute("UPDATE agents SET name = ? WHERE id = ?", (target, row["id"]))
            self._db.commit()
        return self.get_agent(row["id"])

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return dict(row) if row else None

    def get_agent_by_name(self, space_id: str, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM agents WHERE space_id = ? AND name = ?", (space_id, name)
            ).fetchone()
        return dict(row) if row else None

    def list_agents(self, space_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM agents WHERE space_id = ? ORDER BY created_at ASC", (space_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def set_agent_status(self, agent_id: str, status: str) -> dict[str, Any] | None:
        with self._lock:
            self._db.execute("UPDATE agents SET status = ? WHERE id = ?", (status, agent_id))
            self._db.commit()
        return self.get_agent(agent_id)

    # -- messages ----------------------------------------------------------

    def add_message(
        self,
        space_id: str,
        role: str,
        content: str,
        *,
        agent_id: str | None = None,
        to_agent_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE space_id = ?", (space_id,)
            ).fetchone()
            msg_id = _new_id()
            self._db.execute(
                "INSERT INTO messages (id, space_id, seq, role, agent_id, to_agent_id, content,"
                " meta, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    msg_id,
                    space_id,
                    int(row[0]),
                    role,
                    agent_id,
                    to_agent_id,
                    content,
                    json.dumps(meta) if meta else None,
                    _now(),
                ),
            )
            self._db.commit()
        msg = self.get_message(msg_id)
        assert msg is not None
        return msg

    def get_message(self, msg_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["meta"] = _loads(out.get("meta"))
        return out

    def list_messages(
        self, space_id: str, after: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM messages WHERE space_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
                (space_id, after, limit),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["meta"] = _loads(d.get("meta"))
            out.append(d)
        return out

    def max_seq(self, space_id: str) -> int:
        """Highest message seq in a space, or 0 when it is empty.

        An agent rebuilding its context from the transcript uses this as the
        line between "already in my history" and "arrived while I was away".
        """
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM messages WHERE space_id = ?", (space_id,)
            ).fetchone()
        return int(row[0])

    def search(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        needle = (query or "").strip()
        if len(needle) < 2:
            return []
        like = f"%{needle}%"
        hits: list[dict[str, Any]] = []
        with self._lock:
            for row in self._db.execute(
                "SELECT id, title FROM spaces WHERE archived = 0 AND title LIKE ? LIMIT ?",
                (like, limit),
            ):
                hits.append(
                    {
                        "kind": "space",
                        "id": row["id"],
                        "title": row["title"],
                        "snippet": row["title"],
                        "space_id": row["id"],
                    }
                )
            for row in self._db.execute(
                "SELECT id, space_id, name FROM agents WHERE name LIKE ? LIMIT ?",
                (like, limit),
            ):
                hits.append(
                    {
                        "kind": "agent",
                        "id": row["id"],
                        "title": row["name"],
                        "snippet": row["name"],
                        "space_id": row["space_id"],
                    }
                )
            for row in self._db.execute(
                "SELECT id, space_id, role, content FROM messages WHERE content LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (like, limit),
            ):
                body = row["content"] or ""
                hits.append(
                    {
                        "kind": "message",
                        "id": row["id"],
                        "title": row["role"],
                        "snippet": body[:160],
                        "space_id": row["space_id"],
                    }
                )
        return hits[:limit]

    # -- runs --------------------------------------------------------------

    def create_run(self, space_id: str) -> dict[str, Any]:
        run_id = _new_id()
        with self._lock:
            self._db.execute(
                "INSERT INTO runs (id, space_id, started_at) VALUES (?, ?, ?)",
                (run_id, space_id, _now()),
            )
            self._db.commit()
        run = self.get_run(run_id)
        assert run is not None
        return run

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["stats"] = _loads(out.get("stats")) or {}
        return out

    def update_run(
        self, run_id: str, *, status: str | None = None, stats: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        with self._lock:
            if status is not None:
                finished = _now() if status in {"done", "failed", "cancelled"} else None
                self._db.execute(
                    "UPDATE runs SET status = ?, finished_at = COALESCE(?, finished_at)"
                    " WHERE id = ?",
                    (status, finished, run_id),
                )
            if stats is not None:
                self._db.execute(
                    "UPDATE runs SET stats = ? WHERE id = ?", (json.dumps(stats), run_id)
                )
            self._db.commit()
        return self.get_run(run_id)

    def usage_totals(self) -> dict[str, Any]:
        """Sum run stats for the HUD. Missing JSON is zero, never a crash."""
        with self._lock:
            rows = self._db.execute("SELECT stats FROM runs").fetchall()
        totals: dict[str, Any] = {
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
            "by_route": {},
        }
        routes: dict[str, dict[str, Any]] = {}
        for row in rows:
            stats = _loads(row[0]) or {}
            if not isinstance(stats, dict):
                continue
            totals["llm_calls"] += int(stats.get("llm_calls") or 0)
            totals["prompt_tokens"] += int(stats.get("prompt_tokens") or 0)
            totals["completion_tokens"] += int(stats.get("completion_tokens") or 0)
            try:
                totals["cost_usd"] = round(
                    float(totals["cost_usd"]) + float(stats.get("cost_usd") or 0.0), 6
                )
            except (TypeError, ValueError):
                pass
            by_route = stats.get("by_route") or {}
            if isinstance(by_route, dict):
                for name, slot in by_route.items():
                    if not isinstance(slot, dict):
                        continue
                    acc = routes.setdefault(
                        str(name),
                        {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
                    )
                    acc["llm_calls"] += int(slot.get("llm_calls") or 0)
                    acc["prompt_tokens"] += int(slot.get("prompt_tokens") or 0)
                    acc["completion_tokens"] += int(slot.get("completion_tokens") or 0)
                    acc["cost_usd"] = round(
                        float(acc["cost_usd"]) + float(slot.get("cost_usd") or 0.0), 6
                    )
        totals["by_route"] = routes
        totals["tokens"] = int(totals["prompt_tokens"]) + int(totals["completion_tokens"])
        return totals

    # -- confirms ----------------------------------------------------------

    def create_confirm(
        self,
        space_id: str,
        *,
        run_id: str | None,
        agent_id: str | None,
        tool: str,
        args: dict[str, Any] | None,
    ) -> dict[str, Any]:
        confirm_id = _new_id()
        with self._lock:
            self._db.execute(
                "INSERT INTO confirms (id, space_id, run_id, agent_id, tool, args, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (confirm_id, space_id, run_id, agent_id, tool, json.dumps(args or {}), _now()),
            )
            self._db.commit()
        confirm = self.get_confirm(confirm_id)
        assert confirm is not None
        return confirm

    def get_confirm(self, confirm_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM confirms WHERE id = ?", (confirm_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["args"] = _loads(out.get("args")) or {}
        return out

    def decide_confirm(
        self, confirm_id: str, approved: bool, *, takeover: bool = False
    ) -> dict[str, Any] | None:
        status = "takeover" if takeover else ("approved" if approved else "denied")
        with self._lock:
            self._db.execute(
                "UPDATE confirms SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
                (status, _now(), confirm_id),
            )
            self._db.commit()
        return self.get_confirm(confirm_id)

    def pending_confirms(self, space_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM confirms WHERE space_id = ? AND status = 'pending'"
                " ORDER BY created_at ASC",
                (space_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["args"] = _loads(d.get("args")) or {}
            out.append(d)
        return out
