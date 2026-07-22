"""S1 — agent + run registry (ops DB). The manager 'hires' agents here."""
from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import uuid
from typing import Any

from packs.dms.audit.ledger import default_db_path

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(default_db_path()), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _ensure(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS dms_agents ("
        " agent_id TEXT PRIMARY KEY, name TEXT, role_label TEXT, created_by TEXT,"
        " detector_cfg TEXT, report_template TEXT, context_question TEXT,"
        " approver_role TEXT, status TEXT, created_at TEXT)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS dms_agent_runs ("
        " run_id TEXT PRIMARY KEY, agent_id TEXT, status TEXT, detection TEXT,"
        " report TEXT, verdict TEXT, artifact_path TEXT, approver TEXT,"
        " created_at TEXT, updated_at TEXT,"
        " workflow_id TEXT, last_step TEXT)")
    # Additive columns for DBs created before S1 durable-resume.
    cols = {r[1] for r in con.execute("PRAGMA table_info(dms_agent_runs)").fetchall()}
    if "workflow_id" not in cols:
        con.execute("ALTER TABLE dms_agent_runs ADD COLUMN workflow_id TEXT")
    if "last_step" not in cols:
        con.execute("ALTER TABLE dms_agent_runs ADD COLUMN last_step TEXT")
    con.execute(
        "CREATE TABLE IF NOT EXISTS dms_agent_run_steps ("
        " run_id TEXT NOT NULL, step TEXT NOT NULL, output TEXT,"
        " completed_at TEXT,"
        " PRIMARY KEY (run_id, step))")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_dms_agent_runs_workflow_id"
        " ON dms_agent_runs(workflow_id)")


def valid_agent_id(agent_id: str) -> bool:
    return bool(_ID_RE.match(agent_id or ""))


def create_agent(agent_id: str, *, name: str = "", role_label: str = "analyst",
                 created_by: str = "system", detector_cfg: dict | None = None,
                 report_template: str = "", context_question: str = "",
                 approver_role: str = "steward") -> dict[str, Any]:
    if not valid_agent_id(agent_id):
        raise ValueError(f"invalid agent_id {agent_id!r}")
    con = _conn()
    try:
        _ensure(con)
        if con.execute("SELECT 1 FROM dms_agents WHERE agent_id=?", (agent_id,)).fetchone():
            return get_agent(agent_id)  # idempotent
        con.execute(
            "INSERT INTO dms_agents VALUES (?,?,?,?,?,?,?,?,?,?)",
            (agent_id, name or agent_id, role_label, created_by,
             json.dumps(detector_cfg or {}), report_template, context_question,
             approver_role, "active", _now()))
        con.commit()
    finally:
        con.close()
    _audit("agent.created", {"agent_id": agent_id, "created_by": created_by})
    return get_agent(agent_id)


def get_agent(agent_id: str) -> dict[str, Any] | None:
    con = _conn()
    try:
        _ensure(con)
        row = con.execute("SELECT * FROM dms_agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["detector_cfg"] = json.loads(d.get("detector_cfg") or "{}")
        return d
    finally:
        con.close()


def list_agents() -> list[dict[str, Any]]:
    con = _conn()
    try:
        _ensure(con)
        out = []
        for row in con.execute("SELECT * FROM dms_agents ORDER BY created_at DESC").fetchall():
            d = dict(row)
            d["detector_cfg"] = json.loads(d.get("detector_cfg") or "{}")
            out.append(d)
        return out
    finally:
        con.close()


def record_run(agent_id: str, *, status: str, detection: dict, report: str = "",
               verdict: dict | None = None, workflow_id: str | None = None,
               last_step: str = "", run_id: str | None = None) -> str:
    run_id = run_id or uuid.uuid4().hex[:12]
    con = _conn()
    try:
        _ensure(con)
        con.execute(
            "INSERT INTO dms_agent_runs "
            "(run_id, agent_id, status, detection, report, verdict, artifact_path,"
            " approver, created_at, updated_at, workflow_id, last_step)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, agent_id, status, json.dumps(detection), report,
             json.dumps(verdict or {}), None, None, _now(), _now(),
             workflow_id, last_step or None))
        con.commit()
    finally:
        con.close()
    return run_id


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    con = _conn()
    try:
        _ensure(con)
        con.execute(f"UPDATE dms_agent_runs SET {cols} WHERE run_id=?",
                    (*fields.values(), run_id))
        con.commit()
    finally:
        con.close()


def checkpoint_step(run_id: str, step: str, output: dict | None = None) -> None:
    """Persist a completed workflow step (manual durable resume without DBOS)."""
    con = _conn()
    try:
        _ensure(con)
        con.execute(
            "INSERT INTO dms_agent_run_steps (run_id, step, output, completed_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(run_id, step) DO UPDATE SET"
            " output=excluded.output, completed_at=excluded.completed_at",
            (run_id, step, json.dumps(output or {}), _now()))
        con.execute(
            "UPDATE dms_agent_runs SET last_step=?, updated_at=? WHERE run_id=?",
            (step, _now(), run_id))
        con.commit()
    finally:
        con.close()


def get_step(run_id: str, step: str) -> dict[str, Any] | None:
    con = _conn()
    try:
        _ensure(con)
        row = con.execute(
            "SELECT * FROM dms_agent_run_steps WHERE run_id=? AND step=?",
            (run_id, step)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["output"] = json.loads(d.get("output") or "{}")
        return d
    finally:
        con.close()


def get_run_by_workflow_id(workflow_id: str) -> dict[str, Any] | None:
    if not workflow_id:
        return None
    con = _conn()
    try:
        _ensure(con)
        row = con.execute(
            "SELECT * FROM dms_agent_runs WHERE workflow_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (workflow_id,)).fetchone()
        if not row:
            return None
        return _row_to_run(dict(row))
    finally:
        con.close()


def get_run(run_id: str) -> dict[str, Any] | None:
    con = _conn()
    try:
        _ensure(con)
        row = con.execute("SELECT * FROM dms_agent_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        return _row_to_run(dict(row))
    finally:
        con.close()


def _row_to_run(d: dict[str, Any]) -> dict[str, Any]:
    d["detection"] = json.loads(d.get("detection") or "{}")
    d["verdict"] = json.loads(d.get("verdict") or "{}")
    return d


def list_runs(agent_id: str | None = None, *, status: str | None = None,
              limit: int = 100) -> list[dict[str, Any]]:
    con = _conn()
    try:
        _ensure(con)
        q = "SELECT * FROM dms_agent_runs"
        clauses, params = [], []
        if agent_id:
            clauses.append("agent_id=?"); params.append(agent_id)
        if status:
            clauses.append("status=?"); params.append(status)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        out = []
        for row in con.execute(q, params).fetchall():
            out.append(_row_to_run(dict(row)))
        return out
    finally:
        con.close()


def _audit(event: str, payload: dict) -> None:
    try:
        from packs.dms.audit import ledger

        ledger.append(payload.get("created_by", "system"), event, payload)
    except Exception:  # noqa: BLE001
        pass
