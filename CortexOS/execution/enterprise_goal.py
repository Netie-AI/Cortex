"""G2.0 — the ethical goal the engine is allowed to pursue on its own.

An always-on seeker needs a bound objective *and* a spine that says what it may
never do to reach it. Two design decisions carry the weight here:

1. **Constraints cannot be absent.** A caller may add hard constraints but can
   never create a goal without the baseline set, and can never weaken one. A
   goal with no ethical floor would be an agent with no ethical floor.
2. **Collapse never ships anything on its own.** Termination requires the
   measurable predicates to pass *and* no constraint to be violated — the
   `false_pass_caught` rule from gen-cFSM, extended to ethics. A confident model
   that is wrong must not be able to spend money.

Storage mirrors routines/scoreboard: SQLite WAL under data/engine, DB_PATH
monkeypatched in tests, never chdir.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from CortexOS.paths import data_path

DB_PATH = data_path("engine", "goals.db")

_lock = threading.Lock()

# Always present, never removable, never weakened by a caller.
BASELINE_CONSTRAINTS: list[dict[str, str]] = [
    {
        "kind": "no_deception",
        "rule": "Never mislead a person, and never misrepresent who is acting.",
    },
    {
        "kind": "no_illegal",
        "rule": "Never take an action that breaks the law or an agreement.",
    },
    {
        "kind": "no_unconfirmed_irreversible",
        "rule": "Never do something that cannot be undone without a person confirming it first.",
    },
    {
        "kind": "no_secret_exfiltration",
        "rule": "Never send credentials, keys or personal data off this machine.",
    },
    {
        "kind": "no_unconsented_contact",
        "rule": "Never contact anyone on the user's behalf unless they asked for it.",
    },
]

BASELINE_KINDS = {c["kind"] for c in BASELINE_CONSTRAINTS}

# The autonomy ladder. Anything not explicitly safe is confirm-gated — an
# unknown action kind must never fall through to "go ahead".
SAFE_ACTIONS: frozenset[str] = frozenset(
    {
        "inspect",
        "check_metric",
        "summarize",
        "draft_routine",
        "draft_report",
        "propose",
        "review_local",
    }
)

CONFIRM_ACTIONS: frozenset[str] = frozenset(
    {
        "send_message",
        "publish",
        "purchase",
        "transfer_funds",
        "deploy",
        "delete",
        "grant_access",
        "external_call",
        "approve_app",
        "write_external",
    }
)

RISK_SAFE = "safe"
RISK_CONFIRM = "confirm"

DEFAULT_PREFERENCES: dict[str, Any] = {
    "latency": "normal",
    "cost_myr": 5.0,
    "autonomy_level": "draft_only",
}

AUTONOMY_LEVELS = ("draft_only", "safe_auto", "supervised")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _lock, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS goals (
              id TEXT PRIMARY KEY,
              org_id TEXT DEFAULT '',
              statement TEXT NOT NULL,
              measurable_criteria TEXT DEFAULT '[]',
              hard_constraints TEXT DEFAULT '[]',
              soft_preferences TEXT DEFAULT '{}',
              audit_required INTEGER DEFAULT 1,
              active INTEGER DEFAULT 1,
              created_at REAL,
              updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS goal_seeks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              goal_id TEXT NOT NULL,
              trigger TEXT DEFAULT 'manual',
              proposals TEXT DEFAULT '[]',
              assumptions TEXT DEFAULT '[]',
              created_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_goal_seeks ON goal_seeks(goal_id, id);
            """
        )


def _json_load(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw) if raw else fallback
    except Exception:
        return fallback


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["measurable_criteria"] = _json_load(out.get("measurable_criteria"), [])
    out["hard_constraints"] = _json_load(out.get("hard_constraints"), [])
    out["soft_preferences"] = _json_load(out.get("soft_preferences"), {})
    out["audit_required"] = bool(out.get("audit_required"))
    out["active"] = bool(out.get("active"))
    out["constraints_in_words"] = [c["rule"] for c in out["hard_constraints"]]
    return out


def merge_constraints(supplied: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Baseline always wins; extras are kept; a baseline kind can't be redefined."""
    merged: list[dict[str, str]] = [dict(c) for c in BASELINE_CONSTRAINTS]
    for constraint in supplied or []:
        kind = str(constraint.get("kind") or "").strip()
        rule = str(constraint.get("rule") or "").strip()
        if not kind or kind in BASELINE_KINDS:
            continue  # never let a caller overwrite a baseline rule
        merged.append({"kind": kind, "rule": rule or kind.replace("_", " ")})
    return merged


def normalize_criterion(raw: dict[str, Any]) -> dict[str, Any]:
    direction = str(raw.get("direction") or "increase").lower()
    if direction not in ("increase", "decrease", "maintain"):
        direction = "increase"
    return {
        "name": str(raw.get("name") or raw.get("metric") or "unnamed"),
        "metric": str(raw.get("metric") or raw.get("name") or "unnamed"),
        "direction": direction,
        "target": raw.get("target"),
        "floor": raw.get("floor"),
        "evidence_source": str(raw.get("evidence_source") or ""),
    }


def create_goal(
    statement: str,
    *,
    org_id: str = "",
    measurable_criteria: list[dict[str, Any]] | None = None,
    hard_constraints: list[dict[str, Any]] | None = None,
    soft_preferences: dict[str, Any] | None = None,
    audit_required: bool = True,
    goal_id: str | None = None,
) -> dict[str, Any]:
    init()
    statement = (statement or "").strip()
    if not statement:
        return {"ok": False, "error": "goal_statement_required"}

    prefs = {**DEFAULT_PREFERENCES, **(soft_preferences or {})}
    if prefs.get("autonomy_level") not in AUTONOMY_LEVELS:
        prefs["autonomy_level"] = "draft_only"

    gid = goal_id or "goal-" + uuid.uuid4().hex[:8]
    now = time.time()
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT INTO goals (
              id, org_id, statement, measurable_criteria, hard_constraints,
              soft_preferences, audit_required, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gid,
                org_id,
                statement,
                json.dumps([normalize_criterion(c) for c in (measurable_criteria or [])]),
                json.dumps(merge_constraints(hard_constraints)),
                json.dumps(prefs),
                int(bool(audit_required)),
                now,
                now,
            ),
        )
    goal = get_goal(gid)
    from CortexOS.execution import goal_audit

    return {"ok": True, "goal": goal, "audit": goal_audit.goal_bound(goal or {})}


def get_goal(goal_id: str) -> dict[str, Any] | None:
    init()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    return _row(row) if row else None


def list_goals(*, active_only: bool = False) -> list[dict[str, Any]]:
    init()
    sql = "SELECT * FROM goals"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY created_at"
    with _conn() as conn:
        return [_row(r) for r in conn.execute(sql).fetchall()]


def active_goal() -> dict[str, Any] | None:
    goals = list_goals(active_only=True)
    return goals[0] if goals else None


def update_goal(goal_id: str, **fields: Any) -> dict[str, Any] | None:
    init()
    if get_goal(goal_id) is None:
        return None
    sets: dict[str, Any] = {}
    if "statement" in fields and str(fields["statement"] or "").strip():
        sets["statement"] = str(fields["statement"]).strip()
    if "measurable_criteria" in fields and fields["measurable_criteria"] is not None:
        sets["measurable_criteria"] = json.dumps(
            [normalize_criterion(c) for c in fields["measurable_criteria"]]
        )
    if "hard_constraints" in fields and fields["hard_constraints"] is not None:
        # Re-merged, so an update can never strip the baseline either.
        sets["hard_constraints"] = json.dumps(merge_constraints(fields["hard_constraints"]))
    if "soft_preferences" in fields and fields["soft_preferences"] is not None:
        prefs = {**DEFAULT_PREFERENCES, **fields["soft_preferences"]}
        if prefs.get("autonomy_level") not in AUTONOMY_LEVELS:
            prefs["autonomy_level"] = "draft_only"
        sets["soft_preferences"] = json.dumps(prefs)
    if "audit_required" in fields and fields["audit_required"] is not None:
        sets["audit_required"] = int(bool(fields["audit_required"]))
    if "active" in fields and fields["active"] is not None:
        sets["active"] = int(bool(fields["active"]))
    if not sets:
        return get_goal(goal_id)
    changed = [k for k in sets if k != "updated_at"]
    sets["updated_at"] = time.time()
    clause = ", ".join(f"{k} = ?" for k in sets)
    with _lock, _conn() as conn:
        conn.execute(f"UPDATE goals SET {clause} WHERE id = ?", (*sets.values(), goal_id))
    goal = get_goal(goal_id)
    from CortexOS.execution import goal_audit

    goal_audit.goal_updated(goal or {}, changed)
    return goal


def delete_goal(goal_id: str) -> bool:
    init()
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
        conn.execute("DELETE FROM goal_seeks WHERE goal_id = ?", (goal_id,))
    return cur.rowcount > 0


# --- the ethical gate --------------------------------------------------------


def classify_action(action_kind: str | None) -> str:
    """Unknown kinds are confirm-gated — never assume a new verb is harmless."""
    kind = str(action_kind or "").strip().lower()
    return RISK_SAFE if kind in SAFE_ACTIONS else RISK_CONFIRM


def check_constraints(goal: dict[str, Any], violates: list[str] | None) -> list[dict[str, str]]:
    """Return the constraints an action is declared to violate."""
    declared = {str(v).strip().lower() for v in (violates or [])}
    if not declared:
        return []
    return [c for c in goal.get("hard_constraints", []) if c["kind"].lower() in declared]


def gate_action(
    goal: dict[str, Any],
    *,
    action_kind: str,
    predicate_results: list[dict[str, Any]] | None = None,
    violates: list[str] | None = None,
    collapse: float | None = None,
) -> dict[str, Any]:
    """May this action proceed on its own? Constraints first, predicates second,
    collapse never on its own."""
    breached = check_constraints(goal, violates)
    risk = classify_action(action_kind)
    autonomy = str(goal.get("soft_preferences", {}).get("autonomy_level") or "draft_only")

    predicates = predicate_results or []
    predicates_pass = all(bool(p.get("pass")) for p in predicates) if predicates else None

    if breached:
        return {
            "allowed": False,
            "requires_confirm": False,
            "risk": risk,
            "blocked_by": [c["kind"] for c in breached],
            "reasons": [f"Not allowed: {c['rule']}" for c in breached],
            "predicates_pass": predicates_pass,
        }

    if predicates and predicates_pass is False:
        return {
            "allowed": False,
            "requires_confirm": risk == RISK_CONFIRM,
            "risk": risk,
            "blocked_by": ["predicates_failed"],
            "reasons": ["The checks that prove this worked did not pass."],
            "predicates_pass": False,
        }

    if risk == RISK_CONFIRM:
        return {
            "allowed": False,
            "requires_confirm": True,
            "risk": risk,
            "blocked_by": [],
            "reasons": ["This needs your go-ahead before it happens."],
            "predicates_pass": predicates_pass,
        }

    if autonomy == "draft_only":
        return {
            "allowed": False,
            "requires_confirm": True,
            "risk": risk,
            "blocked_by": [],
            "reasons": ["This goal is set to draft only, so nothing runs without you."],
            "predicates_pass": predicates_pass,
        }

    _ = collapse  # deliberately unused: confidence alone never authorises an action
    return {
        "allowed": True,
        "requires_confirm": False,
        "risk": risk,
        "blocked_by": [],
        "reasons": ["Low-risk step, allowed by this goal's autonomy setting."],
        "predicates_pass": predicates_pass,
    }


def _audit_termination(
    goal: dict[str, Any], outcome: dict[str, Any], predicate_results: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Only the refusals are audit events — 'continue' is ordinary operation."""
    if outcome["verdict"] in ("constraint_violated", "false_pass_caught"):
        from CortexOS.execution import goal_audit

        outcome["audit"] = goal_audit.termination_blocked(
            str(goal.get("id") or ""), outcome, predicate_results=predicate_results
        )
    return outcome


def evaluate_termination(
    goal: dict[str, Any],
    *,
    collapse: float,
    predicate_results: list[dict[str, Any]] | None,
    violates: list[str] | None = None,
    tau: float = 0.85,
) -> dict[str, Any]:
    """gen-cFSM's false-pass rule extended to ethics: a confident-looking run
    that failed its checks — or breached a constraint — is not a success."""
    breached = check_constraints(goal, violates)
    predicates = predicate_results or []
    predicates_pass = all(bool(p.get("pass")) for p in predicates) if predicates else False

    if breached:
        return _audit_termination(
            goal,
            {
                "verdict": "constraint_violated",
                "success": False,
                "collapse": collapse,
                "blocked_by": [c["kind"] for c in breached],
                "reasons": [f"Stopped: {c['rule']}" for c in breached],
            },
            predicate_results,
        )
    if collapse >= tau and not predicates_pass:
        return _audit_termination(
            goal,
            {
                "verdict": "false_pass_caught",
                "success": False,
                "collapse": collapse,
                "blocked_by": ["predicates_failed"],
                "reasons": ["It looked finished, but the checks that prove it did not pass."],
            },
            predicate_results,
        )
    if predicates_pass:
        return {
            "verdict": "success",
            "success": True,
            "collapse": collapse,
            "blocked_by": [],
            "reasons": ["Every check passed."],
        }
    return {
        "verdict": "continue",
        "success": False,
        "collapse": collapse,
        "blocked_by": [],
        "reasons": ["Not there yet."],
    }


# --- seek history ------------------------------------------------------------


def record_seek(
    goal_id: str, proposals: list[dict[str, Any]], assumptions: list[str], trigger: str = "manual"
) -> None:
    init()
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO goal_seeks (goal_id, trigger, proposals, assumptions, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (goal_id, trigger, json.dumps(proposals), json.dumps(assumptions), time.time()),
        )
        conn.execute(
            "DELETE FROM goal_seeks WHERE goal_id = ? AND id NOT IN ("
            " SELECT id FROM goal_seeks WHERE goal_id = ? ORDER BY id DESC LIMIT 20)",
            (goal_id, goal_id),
        )


def list_seeks(goal_id: str, limit: int = 10) -> list[dict[str, Any]]:
    init()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM goal_seeks WHERE goal_id = ? ORDER BY id DESC LIMIT ?",
            (goal_id, int(limit)),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["proposals"] = _json_load(item.get("proposals"), [])
        item["assumptions"] = _json_load(item.get("assumptions"), [])
        out.append(item)
    return out
