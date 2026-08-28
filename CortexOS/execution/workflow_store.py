"""Telemetry store for background workflow runs — the Background tasks panel's SoT.

Two jobs: durable history in SQLite (so a panel opened after a run still shows
what happened) and a live in-process fan-out so an SSE endpoint can stream the
same events as they land. Both write through one ``record`` call, so a run can
never be live-streamed as something the database disagrees with.

Timestamps are epoch seconds. Token counts are actuals from the adapter where
the provider reports them, and estimates otherwise — ``tokens_estimated`` says
which, because a cost figure the user cannot trust is worse than no figure.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from CortexOS.paths import data_path

DB_PATH = data_path("workflows", "runs.db")

_TERMINAL = ("completed", "error", "stopped")

_lock = threading.Lock()
_subscribers: dict[str, list[queue.Queue]] = {}
_sub_lock = threading.Lock()


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # WAL shm can IOERR on Windows after a crashed writer in this process;
        # DELETE journal still serves the panel.
        conn.execute("PRAGMA journal_mode=DELETE")
    return conn


def _conn() -> sqlite3.Connection:
    path = DB_PATH if DB_PATH.is_absolute() else data_path("workflows", "runs.db")
    # Refuse cwd-relative paths — WinError 433 when uvicorn cwd is not the repo.
    if not path.is_absolute():
        raise RuntimeError(f"workflow DB path must be absolute, got {path!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    last: sqlite3.OperationalError | None = None
    for attempt in range(3):
        try:
            return _open(path)
        except sqlite3.OperationalError as exc:
            last = exc
            time.sleep(0.05 * (attempt + 1))
    assert last is not None
    raise last


def init() -> None:
    with _lock, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS wf_runs (
              id TEXT PRIMARY KEY,
              template_id TEXT NOT NULL,
              title TEXT DEFAULT '',
              purpose TEXT DEFAULT '',
              status TEXT DEFAULT 'queued',
              origin TEXT DEFAULT 'api',
              session_id TEXT DEFAULT '',
              actor TEXT DEFAULT '',
              prompt TEXT DEFAULT '',
              vars TEXT DEFAULT '{}',
              agent_total INTEGER DEFAULT 0,
              agent_done INTEGER DEFAULT 0,
              prompt_tokens INTEGER DEFAULT 0,
              completion_tokens INTEGER DEFAULT 0,
              tokens_estimated INTEGER DEFAULT 0,
              tool_calls INTEGER DEFAULT 0,
              cost_myr REAL DEFAULT 0,
              result TEXT DEFAULT '',
              error TEXT DEFAULT '',
              created_at REAL,
              started_at REAL,
              finished_at REAL
            );
            CREATE TABLE IF NOT EXISTS wf_phases (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              phase_id TEXT NOT NULL,
              title TEXT DEFAULT '',
              detail TEXT DEFAULT '',
              ordinal INTEGER DEFAULT 0,
              status TEXT DEFAULT 'pending',
              agent_total INTEGER DEFAULT 0,
              agent_done INTEGER DEFAULT 0,
              started_at REAL,
              finished_at REAL,
              UNIQUE(run_id, phase_id)
            );
            CREATE TABLE IF NOT EXISTS wf_agents (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              phase_id TEXT NOT NULL,
              node_id TEXT NOT NULL,
              label TEXT DEFAULT '',
              purpose TEXT DEFAULT '',
              prompt_id TEXT DEFAULT '',
              tools TEXT DEFAULT '[]',
              status TEXT DEFAULT 'pending',
              model TEXT DEFAULT '',
              tier TEXT DEFAULT '',
              steps INTEGER DEFAULT 0,
              prompt_tokens INTEGER DEFAULT 0,
              completion_tokens INTEGER DEFAULT 0,
              tool_calls INTEGER DEFAULT 0,
              cost_myr REAL DEFAULT 0,
              elapsed_ms INTEGER DEFAULT 0,
              excerpt TEXT DEFAULT '',
              error TEXT DEFAULT '',
              started_at REAL,
              finished_at REAL,
              UNIQUE(run_id, node_id)
            );
            CREATE TABLE IF NOT EXISTS wf_tool_calls (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              node_id TEXT NOT NULL,
              tool TEXT NOT NULL,
              ok INTEGER DEFAULT 1,
              ms INTEGER DEFAULT 0,
              summary TEXT DEFAULT '',
              at REAL
            );
            CREATE INDEX IF NOT EXISTS ix_wf_phases_run ON wf_phases(run_id);
            CREATE INDEX IF NOT EXISTS ix_wf_agents_run ON wf_agents(run_id);
            CREATE INDEX IF NOT EXISTS ix_wf_tools_run ON wf_tool_calls(run_id, node_id);
            CREATE INDEX IF NOT EXISTS ix_wf_runs_status ON wf_runs(status, created_at DESC);
            """
        )


# -- live fan-out ------------------------------------------------------------


def subscribe(run_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=512)
    with _sub_lock:
        _subscribers.setdefault(run_id, []).append(q)
    return q


def unsubscribe(run_id: str, q: queue.Queue) -> None:
    with _sub_lock:
        subs = _subscribers.get(run_id)
        if not subs:
            return
        if q in subs:
            subs.remove(q)
        if not subs:
            _subscribers.pop(run_id, None)


def publish(run_id: str, event: dict[str, Any]) -> None:
    """Fan out to live listeners. A listener that has stopped draining is
    dropped rather than allowed to block the run that is producing events."""
    payload = {**event, "run_id": run_id, "at": time.time()}
    with _sub_lock:
        subs = list(_subscribers.get(run_id, ()))
        wildcard = list(_subscribers.get("*", ()))
    for q in subs + wildcard:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass


# -- writes ------------------------------------------------------------------


def create_run(
    run_id: str,
    *,
    template_id: str,
    title: str,
    purpose: str = "",
    prompt: str = "",
    origin: str = "api",
    session_id: str = "",
    actor: str = "",
    variables: dict[str, Any] | None = None,
    phases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    init()
    now = time.time()
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO wf_runs(id,template_id,title,purpose,status,origin,session_id,actor,"
            "prompt,vars,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                template_id,
                title[:200],
                purpose[:400],
                "queued",
                origin[:40],
                str(session_id)[:80],
                str(actor)[:80],
                (prompt or "")[:8000],
                json.dumps(variables or {}, default=str)[:8000],
                now,
            ),
        )
        for ordinal, phase in enumerate(phases or []):
            conn.execute(
                "INSERT OR REPLACE INTO wf_phases(run_id,phase_id,title,detail,ordinal,status,agent_total)"
                " VALUES(?,?,?,?,?,?,?)",
                (
                    run_id,
                    str(phase.get("id")),
                    str(phase.get("title") or "")[:120],
                    str(phase.get("detail") or "")[:300],
                    ordinal,
                    "pending",
                    int(phase.get("agent_total") or 0),
                ),
            )
    publish(run_id, {"type": "run_created", "template_id": template_id, "title": title})
    return get_run(run_id)


def register_agents(run_id: str, agents: list[dict[str, Any]]) -> None:
    """Declare the concrete agents up front so the panel can show 3/8 done."""
    init()
    with _lock, _conn() as conn:
        for spec in agents:
            conn.execute(
                "INSERT OR IGNORE INTO wf_agents(run_id,phase_id,node_id,label,purpose,prompt_id,tools,status)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    str(spec.get("phase") or ""),
                    str(spec.get("node_id") or ""),
                    str(spec.get("label") or "")[:120],
                    str(spec.get("purpose") or "")[:400],
                    str(spec.get("prompt_id") or "")[:80],
                    json.dumps(list(spec.get("tools") or [])),
                    "pending",
                ),
            )
        for phase_id, in conn.execute(
            "SELECT DISTINCT phase_id FROM wf_agents WHERE run_id=?", (run_id,)
        ).fetchall():
            total = conn.execute(
                "SELECT COUNT(*) FROM wf_agents WHERE run_id=? AND phase_id=?", (run_id, phase_id)
            ).fetchone()[0]
            conn.execute(
                "UPDATE wf_phases SET agent_total=? WHERE run_id=? AND phase_id=?",
                (int(total), run_id, phase_id),
            )
        total = conn.execute("SELECT COUNT(*) FROM wf_agents WHERE run_id=?", (run_id,)).fetchone()[0]
        conn.execute("UPDATE wf_runs SET agent_total=? WHERE id=?", (int(total), run_id))
    publish(run_id, {"type": "agents_registered", "count": len(agents)})


def start_run(run_id: str) -> None:
    _update_run(run_id, status="running", started_at=time.time())
    publish(run_id, {"type": "run_started"})


def finish_run(run_id: str, *, status: str, result: str = "", error: str = "") -> None:
    _update_run(
        run_id,
        status=status if status in _TERMINAL else "completed",
        finished_at=time.time(),
        result=(result or "")[:40000],
        error=(error or "")[:2000],
    )
    publish(run_id, {"type": "run_finished", "status": status, "error": error[:300]})


def request_cancel(run_id: str) -> bool:
    """Mark a run stopped so the runner aborts between layers. Returns False
    if the run is unknown or already terminal."""
    init()
    with _lock, _conn() as conn:
        row = conn.execute("SELECT status FROM wf_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return False
        if row["status"] in _TERMINAL:
            return False
        conn.execute(
            "UPDATE wf_runs SET status='stopped', finished_at=? WHERE id=?",
            (time.time(), run_id),
        )
    publish(run_id, {"type": "run_finished", "status": "stopped", "error": "cancelled"})
    return True


def is_cancelled(run_id: str) -> bool:
    run = get_run(run_id)
    return bool(run) and run.get("status") == "stopped"


def phase_started(run_id: str, phase_id: str) -> None:
    init()
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE wf_phases SET status='running', started_at=COALESCE(started_at,?) "
            "WHERE run_id=? AND phase_id=? AND status!='done'",
            (time.time(), run_id, phase_id),
        )
    publish(run_id, {"type": "phase_started", "phase": phase_id})


def phase_finished(run_id: str, phase_id: str) -> None:
    init()
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE wf_phases SET status='done', finished_at=? WHERE run_id=? AND phase_id=?",
            (time.time(), run_id, phase_id),
        )
    publish(run_id, {"type": "phase_finished", "phase": phase_id})


def agent_started(run_id: str, node_id: str, **meta: Any) -> None:
    init()
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO wf_agents(run_id,phase_id,node_id,label,purpose,status,started_at) "
            "VALUES(?,?,?,?,?,'running',?) ON CONFLICT(run_id,node_id) DO UPDATE SET "
            "status='running', started_at=COALESCE(wf_agents.started_at,excluded.started_at)",
            (
                run_id,
                str(meta.get("phase") or ""),
                node_id,
                str(meta.get("label") or node_id)[:120],
                str(meta.get("purpose") or "")[:400],
                time.time(),
            ),
        )
    publish(run_id, {"type": "agent_started", "node": node_id, **meta})


def agent_finished(
    run_id: str,
    node_id: str,
    telemetry: dict[str, Any] | None = None,
    *,
    status: str = "done",
    excerpt: str = "",
    error: str = "",
) -> None:
    """Fold one agent's telemetry into its row, its phase, and the run total."""
    init()
    t = telemetry or {}
    prompt_tokens = int(t.get("prompt_tokens") or 0)
    completion_tokens = int(t.get("completion_tokens") or 0)
    tool_count = int(t.get("tool_count") or len(t.get("tools") or ()))
    now = time.time()
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE wf_agents SET status=?, model=?, tier=?, steps=?, prompt_tokens=?, "
            "completion_tokens=?, tool_calls=?, cost_myr=?, elapsed_ms=?, excerpt=?, error=?, "
            "finished_at=? WHERE run_id=? AND node_id=?",
            (
                status,
                str(t.get("model") or "")[:80],
                str(t.get("tier") or "")[:24],
                int(t.get("steps") or 0),
                prompt_tokens,
                completion_tokens,
                tool_count,
                float(t.get("cost_myr") or 0.0),
                int(t.get("elapsed_ms") or 0),
                (excerpt or "")[:4000],
                (error or str(t.get("error") or ""))[:1000],
                now,
                run_id,
                node_id,
            ),
        )
        for call in t.get("tools") or ():
            conn.execute(
                "INSERT INTO wf_tool_calls(run_id,node_id,tool,ok,ms,summary,at) VALUES(?,?,?,?,?,?,?)",
                (
                    run_id,
                    node_id,
                    str(call.get("tool") or "")[:60],
                    1 if call.get("ok", True) else 0,
                    int(call.get("ms") or 0),
                    str(call.get("summary") or "")[:400],
                    now,
                ),
            )
        row = conn.execute(
            "SELECT phase_id FROM wf_agents WHERE run_id=? AND node_id=?", (run_id, node_id)
        ).fetchone()
        phase_id = row["phase_id"] if row else ""
        if phase_id:
            done = conn.execute(
                "SELECT COUNT(*) FROM wf_agents WHERE run_id=? AND phase_id=? AND status IN ('done','error')",
                (run_id, phase_id),
            ).fetchone()[0]
            conn.execute(
                "UPDATE wf_phases SET agent_done=? WHERE run_id=? AND phase_id=?",
                (int(done), run_id, phase_id),
            )
        conn.execute(
            "UPDATE wf_runs SET prompt_tokens=prompt_tokens+?, completion_tokens=completion_tokens+?, "
            "tool_calls=tool_calls+?, cost_myr=cost_myr+?, agent_done=agent_done+1 WHERE id=?",
            (prompt_tokens, completion_tokens, tool_count, float(t.get("cost_myr") or 0.0), run_id),
        )
    publish(
        run_id,
        {
            "type": "agent_finished",
            "node": node_id,
            "status": status,
            "telemetry": t,
            "error": error[:300],
        },
    )


def note_tool_call(run_id: str, node_id: str, tool: str, *, ok: bool, ms: int) -> None:
    """Live breadcrumb while an agent is mid-loop; the durable row is written
    when the agent finishes, so this only drives the panel's live counter."""
    publish(run_id, {"type": "agent_tool", "node": node_id, "tool": tool, "ok": ok, "ms": ms})


def _update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    init()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock, _conn() as conn:
        conn.execute(f"UPDATE wf_runs SET {cols} WHERE id=?", (*fields.values(), run_id))


def mark_tokens_estimated(run_id: str) -> None:
    _update_run(run_id, tokens_estimated=1)


# -- reads -------------------------------------------------------------------


def get_run(run_id: str) -> dict[str, Any]:
    init()
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM wf_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return {}
        run = _run_row(dict(row))
        run["phases"] = [
            dict(r) for r in conn.execute(
                "SELECT * FROM wf_phases WHERE run_id=? ORDER BY ordinal, id", (run_id,)
            ).fetchall()
        ]
        agents = [
            _agent_row(dict(r)) for r in conn.execute(
                "SELECT * FROM wf_agents WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        ]
        by_node: dict[str, list[dict[str, Any]]] = {}
        for r in conn.execute(
            "SELECT * FROM wf_tool_calls WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall():
            by_node.setdefault(r["node_id"], []).append(dict(r))
        for agent in agents:
            agent["tool_detail"] = by_node.get(agent["node_id"], [])
        run["agents"] = agents
        for phase in run["phases"]:
            phase["agents"] = [a for a in agents if a["phase_id"] == phase["phase_id"]]
    return run


def list_runs(*, limit: int = 40, status: str | None = None) -> list[dict[str, Any]]:
    init()
    with _lock, _conn() as conn:
        if status == "active":
            rows = conn.execute(
                "SELECT * FROM wf_runs WHERE status IN ('queued','running') ORDER BY created_at DESC LIMIT ?",
                (min(limit, 200),),
            ).fetchall()
        elif status:
            rows = conn.execute(
                "SELECT * FROM wf_runs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, min(limit, 200)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wf_runs ORDER BY created_at DESC LIMIT ?", (min(limit, 200),)
            ).fetchall()
        out = []
        for row in rows:
            run = _run_row(dict(row))
            run["phases"] = [
                dict(r) for r in conn.execute(
                    "SELECT phase_id,title,detail,ordinal,status,agent_total,agent_done,started_at,finished_at"
                    " FROM wf_phases WHERE run_id=? ORDER BY ordinal, id",
                    (run["id"],),
                ).fetchall()
            ]
            out.append(run)
    return out


def stream(run_id: str, *, keepalive: float = 15.0) -> Iterator[dict[str, Any]]:
    """Yield live events until the run reaches a terminal state.

    Replays a snapshot first so a panel that connects mid-run is not blank
    until the next event happens to fire.
    """
    q = subscribe(run_id)
    try:
        snapshot = get_run(run_id)
        yield {"type": "snapshot", "run": snapshot}
        if snapshot.get("status") in _TERMINAL:
            return
        while True:
            try:
                event = q.get(timeout=keepalive)
            except queue.Empty:
                yield {"type": "keepalive"}
                if (get_run(run_id) or {}).get("status") in _TERMINAL:
                    return
                continue
            yield event
            if event.get("type") == "run_finished":
                return
    finally:
        unsubscribe(run_id, q)


def clear_finished(*, keep: int = 0) -> int:
    """Drop finished runs from history. Returns how many went."""
    init()
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT id FROM wf_runs WHERE status IN ('completed','error','stopped') "
            "ORDER BY created_at DESC LIMIT -1 OFFSET ?",
            (max(0, int(keep)),),
        ).fetchall()
        ids = [r["id"] for r in rows]
        for run_id in ids:
            conn.execute("DELETE FROM wf_tool_calls WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM wf_agents WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM wf_phases WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM wf_runs WHERE id=?", (run_id,))
    return len(ids)


def _run_row(row: dict[str, Any]) -> dict[str, Any]:
    row["tokens"] = int(row.get("prompt_tokens") or 0) + int(row.get("completion_tokens") or 0)
    row["tokens_estimated"] = bool(row.get("tokens_estimated"))
    started = row.get("started_at") or row.get("created_at") or 0
    end = row.get("finished_at") or time.time()
    row["elapsed_s"] = round(max(0.0, end - started), 1) if started else 0.0
    try:
        row["vars"] = json.loads(row.get("vars") or "{}")
    except (TypeError, ValueError):
        row["vars"] = {}
    return row


def _agent_row(row: dict[str, Any]) -> dict[str, Any]:
    row["tokens"] = int(row.get("prompt_tokens") or 0) + int(row.get("completion_tokens") or 0)
    try:
        row["tools"] = json.loads(row.get("tools") or "[]")
    except (TypeError, ValueError):
        row["tools"] = []
    return row
