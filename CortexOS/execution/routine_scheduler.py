"""Routine scheduler — the easy-task tier of the agent engine, governed.

Routines are the "simple DAG every time" tier (search → fetch → generate
style): a stored prompt + preset + basic/high/max depth on an interval,
executed through the existing run-plan dispatch — never a new orchestrator.
Everything runs local and parallel on asyncio (no goroutines, no second
runtime); the governor is the AI-agent hook that pauses a routine on an
error streak or a blown daily cost cap instead of letting it burn quietly.

AirGPT's Routines page is a view over this store (data/engine/routines.db).
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any

from CortexOS.execution import humanize, schedule_spec
from CortexOS.execution.preset_router import plan_for_request
from CortexOS.execution.run_plan import execute_run_plan
from CortexOS.paths import data_path

DB_PATH = data_path("engine", "routines.db")

DEPTHS = ("basic", "high", "max")
GOVERNOR_ERROR_STREAK = 3
DEFAULT_DAILY_COST_CAP_MYR = 5.0
DEFAULT_POLL_SECONDS = 15
DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_PER_TICK = 10
KEEP_RUNS_PER_ROUTINE = 50
RUN_LEASE_SECONDS = 600  # a 'running' row older than this is a crashed run, not a live one
GLOBAL_DAILY_COST_CAP_MYR = float(os.getenv("CORTEX_ROUTINES_DAILY_CAP_MYR", "25.0"))

_lock = threading.Lock()
_sched_thread: threading.Thread | None = None
_sched_stop: threading.Event | None = None

_UPDATABLE = {
    "name",
    "prompt",
    "preset",
    "depth",
    "interval_seconds",
    "enabled",
    "daily_cost_cap_myr",
    "vars",
    "cost_today",
    "cost_day",
    "status",
    "paused_reason",
    "error_streak",
    "last_run_at",
    "next_run_at",
    "running_since",
    "timeout_seconds",
    "schedule",
    "predicates",
    "current_run_id",
}


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Additive migration for DBs created by older builds (e.g. the live engine)."""
    have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init() -> None:
    from CortexOS.packaging import require_extra

    require_extra("agentic", feature="routines")
    with _lock, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS routines (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              prompt TEXT NOT NULL,
              preset TEXT DEFAULT 'minimal',
              depth TEXT DEFAULT 'basic',
              interval_seconds INTEGER DEFAULT 3600,
              enabled INTEGER DEFAULT 1,
              status TEXT DEFAULT 'idle',
              paused_reason TEXT DEFAULT '',
              error_streak INTEGER DEFAULT 0,
              cost_today REAL DEFAULT 0,
              cost_day TEXT DEFAULT '',
              daily_cost_cap_myr REAL DEFAULT 5.0,
              vars TEXT DEFAULT '{}',
              last_run_at REAL,
              next_run_at REAL,
              created_at REAL,
              running_since REAL,
              timeout_seconds REAL DEFAULT 300,
              schedule TEXT DEFAULT '',
              predicates TEXT DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS routine_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              routine_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              status TEXT DEFAULT '',
              started_at REAL,
              finished_at REAL,
              output TEXT DEFAULT '',
              error TEXT DEFAULT '',
              cost_myr REAL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_routine_runs ON routine_runs(routine_id, id);
            """
        )
        _ensure_columns(
            conn,
            "routines",
            {
                "running_since": "REAL",
                "timeout_seconds": f"REAL DEFAULT {int(DEFAULT_TIMEOUT_SECONDS)}",
                "schedule": "TEXT DEFAULT ''",
                "predicates": "TEXT DEFAULT '[]'",
                "current_run_id": "TEXT DEFAULT ''",
            },
        )


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    try:
        out["vars"] = json.loads(out.get("vars") or "{}")
    except Exception:
        out["vars"] = {}
    try:
        out["predicates"] = json.loads(out.get("predicates") or "[]")
    except Exception:
        out["predicates"] = []
    try:
        out["schedule"] = json.loads(out["schedule"]) if out.get("schedule") else None
    except Exception:
        out["schedule"] = None
    out["enabled"] = bool(out.get("enabled"))
    out["schedule_text"] = (
        schedule_spec.describe(out["schedule"])
        if out.get("schedule")
        else f"Every {int(out.get('interval_seconds') or 3600) // 60} minutes"
    )
    out["state"] = humanize.routine_state(out)
    return out


def create_routine(
    name: str,
    prompt: str,
    *,
    preset: str = "minimal",
    depth: str = "basic",
    interval_seconds: int = 3600,
    daily_cost_cap_myr: float = DEFAULT_DAILY_COST_CAP_MYR,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    routine_id: str | None = None,
    vars: dict[str, Any] | None = None,
    schedule: dict[str, Any] | None = None,
    predicates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    init()
    rid = routine_id or "rt-" + uuid.uuid4().hex[:8]
    now = time.time()
    if depth not in DEPTHS:
        depth = "basic"
    if schedule:
        schedule = schedule_spec.normalize_spec(schedule)
        interval_seconds = schedule_spec.approx_interval_seconds(schedule)
        next_run_at = schedule_spec.next_occurrence(schedule, now)
    else:
        next_run_at = now  # plain-interval routines stay due immediately, as before
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT INTO routines (
              id, name, prompt, preset, depth, interval_seconds,
              daily_cost_cap_myr, timeout_seconds, vars, next_run_at,
              created_at, cost_day, schedule, predicates
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid,
                name,
                prompt,
                preset,
                depth,
                int(interval_seconds),
                float(daily_cost_cap_myr),
                float(timeout_seconds),
                json.dumps(vars or {}),
                next_run_at,
                now,
                _today(),
                json.dumps(schedule) if schedule else "",
                json.dumps(predicates or []),
            ),
        )
    return get_routine(rid)  # type: ignore[return-value]


def create_from_goal(goal: str, **overrides: Any) -> dict[str, Any]:
    """One sentence → a live routine, with every knob filled in for the user."""
    from CortexOS.execution import routine_composer

    draft = routine_composer.compose(goal)
    explicit = {k: v for k, v in overrides.items() if v is not None}
    if "interval_seconds" in explicit:
        # An explicit interval beats an inferred calendar schedule.
        draft["schedule"] = None
    draft.update(explicit)
    vars_merged = dict(draft.get("vars") or {})
    if "vars" in explicit and isinstance(explicit.get("vars"), dict):
        vars_merged.update(explicit["vars"])
    kinds = draft.get("work_kinds") or []
    if kinds:
        vars_merged["work_kinds"] = kinds
    routine = create_routine(
        draft["name"],
        draft["prompt"],
        preset=draft["preset"],
        depth=draft["depth"],
        interval_seconds=draft["interval_seconds"],
        daily_cost_cap_myr=draft["daily_cost_cap_myr"],
        timeout_seconds=draft["timeout_seconds"],
        schedule=draft["schedule"],
        predicates=draft["predicates"],
        vars=vars_merged or None,
    )
    routine["assumptions"] = draft["assumptions"]
    routine["work_kinds"] = kinds
    return routine


def get_routine(rid: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM routines WHERE id = ?", (rid,)).fetchone()
    return _row_to_dict(row) if row else None


def list_routines() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM routines ORDER BY created_at").fetchall()
    out = [_row_to_dict(r) for r in rows]
    for item in out:
        runs = list_runs(item["id"], limit=1)
        if runs:
            item["last_run"] = _public_run(runs[0])
    return out


def update_routine(rid: str, **fields: Any) -> dict[str, Any] | None:
    sets = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not sets:
        return get_routine(rid)
    if "vars" in sets and isinstance(sets["vars"], dict):
        sets["vars"] = json.dumps(sets["vars"])
    if "predicates" in sets and isinstance(sets["predicates"], list):
        sets["predicates"] = json.dumps(sets["predicates"])
    if "schedule" in sets and isinstance(sets["schedule"], dict):
        spec = schedule_spec.normalize_spec(sets["schedule"])
        sets["schedule"] = json.dumps(spec)
        sets.setdefault("interval_seconds", schedule_spec.approx_interval_seconds(spec))
        sets.setdefault("next_run_at", schedule_spec.next_occurrence(spec, time.time()))
    if "enabled" in sets:
        sets["enabled"] = int(bool(sets["enabled"]))
    clause = ", ".join(f"{k} = ?" for k in sets)
    with _lock, _conn() as conn:
        conn.execute(f"UPDATE routines SET {clause} WHERE id = ?", (*sets.values(), rid))
    return get_routine(rid)


def delete_routine(rid: str) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM routines WHERE id = ?", (rid,))
        conn.execute("DELETE FROM routine_runs WHERE routine_id = ?", (rid,))
    return cur.rowcount > 0


def pause(rid: str, reason: str = "user") -> dict[str, Any] | None:
    return update_routine(rid, status="paused", paused_reason=reason)


def resume(rid: str) -> dict[str, Any] | None:
    return update_routine(rid, status="idle", paused_reason="", error_streak=0)


def _public_run(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("output") or ""
    text = str(raw)
    steps: list[dict[str, Any]] = []
    kinds: list[str] = []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) and raw[:1] in "{[" else None
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        text = str(parsed.get("text") or parsed.get("output") or "")
        steps = list(parsed.get("steps") or [])
        kinds = list(parsed.get("work_kinds") or [])
        if not text and parsed.get("text") is None:
            text = json.dumps(parsed, default=str)[:4000]
    elif isinstance(parsed, str):
        text = parsed
    return {
        "status": row.get("status"),
        "finished_at": row.get("finished_at"),
        "error": row.get("error") or "",
        "text": text[:4000],
        "steps": steps,
        "work_kinds": kinds,
    }


def list_runs(rid: str, limit: int = 20) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM routine_runs WHERE routine_id = ? ORDER BY id DESC LIMIT ?",
            (rid, int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def _run_by_id(run_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM routine_runs WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    return dict(row) if row else None


def _extract_cost(result: dict[str, Any]) -> float:
    nodes = result.get("nodes")
    if isinstance(nodes, dict):
        return float(sum(float(n.get("cost_myr") or 0.0) for n in nodes.values()))
    return float(result.get("cost_myr") or 0.0)


def apply_governor(rid: str) -> str | None:
    """The governor agent's deterministic floor: streaks and cost caps pause."""
    routine = get_routine(rid)
    if routine is None or routine["status"] == "paused":
        return None
    if routine["error_streak"] >= GOVERNOR_ERROR_STREAK:
        pause(rid, f"governor:error_streak:{routine['error_streak']}")
        return "paused:error_streak"
    cap = float(routine.get("daily_cost_cap_myr") or 0.0)
    if cap > 0 and float(routine.get("cost_today") or 0.0) > cap:
        pause(rid, "governor:cost_cap")
        return "paused:cost_cap"
    return None


def _record_action_event(
    routine: dict[str, Any],
    result: dict[str, Any],
    *,
    ok: bool,
    cost: float,
    started: float,
    finished: float,
) -> None:
    """G2.4 telemetry for scheduled work. Numbers only — no prompt, no output."""
    from CortexOS.execution import action_event, scoreboard

    try:
        predicates = routine.get("predicates") or []
        action_event.record(
            initiative=(
                action_event.INITIATIVE_REACTIVE
                if (routine.get("vars") or {}).get("fire_source")
                else action_event.INITIATIVE_SCHEDULED
            ),
            outcome=action_event.OUTCOME_SUCCEEDED if ok else action_event.OUTCOME_FAILED,
            action_kind=f"routine_{routine.get('preset') or 'minimal'}",
            source="routine",
            goal_family=scoreboard.family_id(str(routine.get("prompt") or "")),
            band=str((routine.get("vars") or {}).get("osr_band") or ""),
            path=str(result.get("chosen") or routine.get("preset") or ""),
            run_id=str(result.get("run_id") or ""),
            predicates_total=len(predicates),
            predicates_passed=len(predicates) if ok else 0,
            cost_myr=cost,
            latency_ms=int((finished - started) * 1000),
        )
    except Exception:
        pass  # telemetry must never break the run it is describing


async def _dispatch(
    routine: dict[str, Any],
    prompt: str,
    body: dict[str, Any],
    predicates: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """'auto' means the engine picks — first run races, later runs reuse the winner."""
    from CortexOS.execution import scheduled_work

    kinds = scheduled_work.classify(prompt)
    stored = (routine.get("vars") or {}).get("work_kinds") or []
    if stored:
        kinds = frozenset(str(k) for k in stored) | kinds
    if scheduled_work.is_operator_work(kinds):
        return await asyncio.to_thread(scheduled_work.run, prompt, kinds=kinds)
    if str(routine.get("preset") or "") == "auto":
        from CortexOS.execution import race_router

        out = await race_router.auto_route(prompt, body, predicates=predicates)
        result = out.get("result") or (out.get("scaled") or {}).get("result") or {}
        return {
            "ok": bool(result.get("ok", out.get("ok"))),
            "status": result.get("status") or out.get("mode"),
            "output": result.get("output"),
            "nodes": result.get("nodes"),
            "error": result.get("error") or "",
            "chosen": out.get("winner"),
        }
    plan = plan_for_request(routine["preset"], body)
    return await execute_run_plan(plan, body)


async def run_once(
    rid: str,
    *,
    now: float | None = None,
    prompt_override: str | None = None,
    extra_vars: dict[str, Any] | None = None,
) -> dict[str, Any]:
    routine = get_routine(rid)
    if routine is None:
        return {"ok": False, "routine_id": rid, "error": "unknown_routine"}
    if routine["status"] == "paused" or not routine["enabled"]:
        return {"ok": False, "routine_id": rid, "error": "not_runnable"}

    now = now or time.time()
    run_id = f"routine:{rid}:{uuid.uuid4().hex[:8]}"
    if routine["status"] == "running":
        since = float(routine.get("running_since") or 0.0)
        if now - since < RUN_LEASE_SECONDS:
            return {"ok": False, "routine_id": rid, "error": "already_running"}
        # Stale lease: reuse the crashed attempt's run_id. If that attempt
        # already committed, replay -- do not mint a second side effect.
        stale_id = str(routine.get("current_run_id") or "")
        if stale_id:
            prior = _run_by_id(stale_id)
            if prior and prior.get("status") == "ok":
                update_routine(
                    rid, status="idle", running_since=None, current_run_id=None
                )
                return {
                    "ok": True,
                    "routine_id": rid,
                    "run_id": stale_id,
                    "status": prior.get("status"),
                    "error": "",
                    "idempotent_replay": True,
                    "explain": None,
                    "chosen": None,
                    "cost_myr": float(prior.get("cost_myr") or 0.0),
                    "governor": None,
                    "next_run_at": routine.get("next_run_at"),
                }
            run_id = stale_id
    prompt = prompt_override if prompt_override is not None else routine["prompt"]
    vars_merged = {**(routine.get("vars") or {}), **(extra_vars or {})}
    body: dict[str, Any] = {
        "prompt": prompt,
        "session_id": run_id,
        "depth": routine["depth"],
        "params": {
            "depth": routine["depth"],
            "idempotency_key": run_id,
            **vars_merged,
        },
    }
    update_routine(rid, status="running", running_since=now, current_run_id=run_id)
    timeout_s = float(routine.get("timeout_seconds") or 0.0)
    predicates = routine.get("predicates") or None
    started = time.time()
    try:
        coro = _dispatch(routine, prompt, body, predicates)
        result = await (asyncio.wait_for(coro, timeout_s) if timeout_s > 0 else coro)
    except asyncio.TimeoutError:
        result = {"ok": False, "error": f"timeout_after:{timeout_s:g}s"}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    finished = time.time()

    ok = bool(result.get("ok"))
    if ok and predicates:
        # "It ran" is not "it worked" — an empty answer is a failed run, and the
        # governor should see it as one.
        from CortexOS.execution.race_router import eval_predicates

        if not eval_predicates(result.get("output"), predicates):
            ok = False
            result = dict(result)
            result["error"] = "goal_not_met"
    cost = _extract_cost(result)
    cost_today = float(routine.get("cost_today") or 0.0)
    if routine.get("cost_day") != _today():
        cost_today = 0.0
    spec = routine.get("schedule")
    next_run_at = (
        schedule_spec.next_occurrence(spec, now)
        if spec
        else now + int(routine["interval_seconds"])
    )
    update_routine(
        rid,
        status="idle",
        running_since=None,
        current_run_id=None,
        last_run_at=now,
        next_run_at=next_run_at,
        error_streak=0 if ok else int(routine["error_streak"]) + 1,
        cost_today=cost_today + cost,
        cost_day=_today(),
    )
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT INTO routine_runs (
              routine_id, run_id, status, started_at, finished_at, output, error, cost_myr
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid,
                run_id,
                "ok" if ok else "error",
                started,
                finished,
                json.dumps(
                    {
                        "text": result.get("output"),
                        "steps": result.get("steps") or [],
                        "work_kinds": result.get("work_kinds") or [],
                    },
                    default=str,
                )[:20000]
                if result.get("steps") is not None
                else json.dumps(result.get("output"), default=str)[:20000],
                str(result.get("error") or "")[:1000],
                cost,
            ),
        )
        conn.execute(
            """
            DELETE FROM routine_runs WHERE routine_id = ? AND id NOT IN (
              SELECT id FROM routine_runs WHERE routine_id = ?
              ORDER BY id DESC LIMIT ?
            )
            """,
            (rid, rid, int(KEEP_RUNS_PER_ROUTINE)),
        )
    _record_action_event(routine, result, ok=ok, cost=cost, started=started, finished=finished)
    governor_action = apply_governor(rid)
    error = str(result.get("error") or "")
    return {
        "ok": ok,
        "routine_id": rid,
        "run_id": run_id,
        "status": result.get("status"),
        "error": error,
        "explain": humanize.explain(error) if error else None,
        "chosen": result.get("chosen"),
        "cost_myr": cost,
        "governor": governor_action,
        "next_run_at": next_run_at,
        "output": result.get("output"),
        "steps": result.get("steps") or [],
        "work_kinds": result.get("work_kinds") or [],
    }


async def tick(now: float | None = None, *, max_runs: int = MAX_PER_TICK) -> list[dict[str, Any]]:
    """Run everything due — capped per tick, gated by the engine-wide budget."""
    init()
    now = now or time.time()
    if global_budget_state()["exhausted"]:
        return []
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id FROM routines
            WHERE enabled = 1 AND status NOT IN ('paused', 'running')
              AND next_run_at <= ?
            ORDER BY next_run_at LIMIT ?
            """,
            (now, int(max_runs)),
        ).fetchall()
    results = []
    for row in rows:
        results.append(await run_once(row["id"], now=now))
    return results


def global_budget_state() -> dict[str, Any]:
    """Engine-wide daily spend across all routines — the hard stop above per-routine caps."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_today), 0) FROM routines WHERE cost_day = ?",
            (_today(),),
        ).fetchone()
    spent = float(row[0] or 0.0)
    cap = float(GLOBAL_DAILY_COST_CAP_MYR)
    return {
        "spent_today_myr": round(spent, 6),
        "cap_myr": cap,
        "exhausted": cap > 0 and spent >= cap,
    }


def pause_all(reason: str = "user:pause_all") -> int:
    """Kill switch: park every routine at once. Returns how many were paused."""
    init()
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE routines SET status = 'paused', paused_reason = ? WHERE status != 'paused'",
            (reason,),
        )
    return cur.rowcount


def resume_all() -> int:
    """Resume user-paused routines only — governor pauses represent real failures
    and stay parked until someone looks at that routine individually."""
    init()
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE routines SET status = 'idle', paused_reason = '', error_streak = 0 "
            "WHERE status = 'paused' AND paused_reason NOT LIKE 'governor:%'"
        )
    return cur.rowcount


def start(poll_seconds: int = DEFAULT_POLL_SECONDS) -> bool:
    """Background poll loop — local, daemon, one per process."""
    global _sched_thread, _sched_stop
    if _sched_thread is not None and _sched_thread.is_alive():
        return False
    stop_event = threading.Event()

    def _loop() -> None:
        while not stop_event.wait(poll_seconds):
            try:
                asyncio.run(tick())
            except Exception:
                pass  # a broken tick must never kill the scheduler thread

    thread = threading.Thread(target=_loop, name="routine-scheduler", daemon=True)
    thread.start()
    _sched_thread = thread
    _sched_stop = stop_event
    return True


def stop() -> None:
    global _sched_thread, _sched_stop
    if _sched_stop is not None:
        _sched_stop.set()
    _sched_thread = None
    _sched_stop = None
