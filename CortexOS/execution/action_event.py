"""G2.4 — ActionEvent telemetry: what the engine actually did, compactly.

One row per real run. It exists for two jobs:

* **teach the ranking from reality** — a run that met its predicates is weak
  evidence that the kind of action was worth doing, so every event feeds
  `action_value` as an **inferred** outcome (never explicit — product law 6);
* **stay trainable without growing forever** — raw events are kept for a recent
  window, then compacted into per-day aggregates.

The compaction is designed around one rule: **it must be lossless for the
fields ranking depends on.** Ranking reads counts of successes and failures per
`(family, action_kind, source)`. Those survive compaction exactly; only the
per-run detail (timing, cost, individual ids) is averaged away. So an old
engine and a compacted one rank identically.

Same privacy policy as `goal_audit`: identifiers, verdicts and numbers — never
prompts, outputs, titles or any other prose.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

from CortexOS.execution import action_value
from CortexOS.paths import data_path

DB_PATH = data_path("engine", "action_events.db")

RAW_RETENTION_DAYS = 14
MAX_RAW_EVENTS = 20000

INITIATIVE_PROACTIVE = "proactive"  # the engine started it (seeker)
INITIATIVE_REACTIVE = "reactive"  # something arrived (/fire, webhook)
INITIATIVE_SCHEDULED = "scheduled"  # a routine came due
INITIATIVES = (INITIATIVE_PROACTIVE, INITIATIVE_REACTIVE, INITIATIVE_SCHEDULED)

OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"

_lock = threading.Lock()


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
            CREATE TABLE IF NOT EXISTS action_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts REAL NOT NULL,
              day TEXT NOT NULL,
              run_id TEXT DEFAULT '',
              goal_id TEXT DEFAULT '',
              goal_family TEXT DEFAULT '',
              initiative TEXT NOT NULL,
              band TEXT DEFAULT '',
              action_kind TEXT DEFAULT '',
              source TEXT DEFAULT '',
              path TEXT DEFAULT '',
              outcome TEXT NOT NULL,
              predicates_total INTEGER DEFAULT 0,
              predicates_passed INTEGER DEFAULT 0,
              collapse REAL,
              cost_myr REAL DEFAULT 0,
              latency_ms INTEGER DEFAULT 0,
              tokens INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_action_events_family
              ON action_events(goal_family, action_kind);
            CREATE INDEX IF NOT EXISTS idx_action_events_day ON action_events(day);

            CREATE TABLE IF NOT EXISTS action_event_daily (
              day TEXT NOT NULL,
              goal_family TEXT DEFAULT '',
              initiative TEXT NOT NULL,
              band TEXT DEFAULT '',
              action_kind TEXT DEFAULT '',
              source TEXT DEFAULT '',
              runs INTEGER DEFAULT 0,
              succeeded INTEGER DEFAULT 0,
              failed INTEGER DEFAULT 0,
              predicates_total INTEGER DEFAULT 0,
              predicates_passed INTEGER DEFAULT 0,
              sum_collapse REAL DEFAULT 0,
              collapse_n INTEGER DEFAULT 0,
              sum_cost_myr REAL DEFAULT 0,
              sum_latency_ms INTEGER DEFAULT 0,
              sum_tokens INTEGER DEFAULT 0,
              PRIMARY KEY (day, goal_family, initiative, band, action_kind, source)
            );
            """
        )


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def record(
    *,
    initiative: str,
    outcome: str,
    action_kind: str = "",
    source: str = "",
    goal_id: str = "",
    goal_family: str = "",
    band: str = "",
    path: str = "",
    run_id: str = "",
    predicates_total: int = 0,
    predicates_passed: int = 0,
    collapse: float | None = None,
    cost_myr: float = 0.0,
    latency_ms: int = 0,
    tokens: int = 0,
    teach: bool = True,
    now: float | None = None,
) -> dict[str, Any]:
    """Record one run and (optionally) let the ranking learn from it — weakly."""
    init()
    if initiative not in INITIATIVES:
        return {"ok": False, "error": f"unknown_initiative:{initiative}"}
    if outcome not in (OUTCOME_SUCCEEDED, OUTCOME_FAILED):
        return {"ok": False, "error": f"unknown_outcome:{outcome}"}

    ts = time.time() if now is None else now
    with _lock, _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO action_events (
              ts, day, run_id, goal_id, goal_family, initiative, band, action_kind,
              source, path, outcome, predicates_total, predicates_passed, collapse,
              cost_myr, latency_ms, tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                _day(ts),
                run_id,
                goal_id,
                goal_family,
                initiative,
                band,
                action_kind,
                source,
                path,
                outcome,
                int(predicates_total),
                int(predicates_passed),
                None if collapse is None else float(collapse),
                float(cost_myr),
                int(latency_ms),
                int(tokens),
            ),
        )
        event_id = cur.lastrowid

    taught = None
    if teach and goal_family and action_kind:
        # Inferred, never explicit: a finished run is a hint, not a decision.
        taught = action_value.record_outcome(
            goal_family,
            action_kind,
            source or "run",
            outcome,
            kind=action_value.KIND_INFERRED,
        )

    return {"ok": True, "event_id": event_id, "taught": taught}


def list_events(limit: int = 50, *, goal_family: str | None = None) -> list[dict[str, Any]]:
    init()
    sql = "SELECT * FROM action_events"
    params: tuple[Any, ...] = ()
    if goal_family:
        sql += " WHERE goal_family = ?"
        params = (goal_family,)
    sql += " ORDER BY id DESC LIMIT ?"
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, (*params, int(limit))).fetchall()]


def daily(goal_family: str | None = None) -> list[dict[str, Any]]:
    init()
    sql = "SELECT * FROM action_event_daily"
    params: tuple[Any, ...] = ()
    if goal_family:
        sql += " WHERE goal_family = ?"
        params = (goal_family,)
    sql += " ORDER BY day DESC"
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def outcome_counts(goal_family: str, action_kind: str, source: str = "") -> dict[str, int]:
    """Successes and failures for one action — reading raw + compacted together.

    This is the function that proves compaction is lossless where it matters:
    it returns the same numbers before and after events are rolled up.
    """
    init()
    where = "goal_family = ? AND action_kind = ?"
    params: list[Any] = [goal_family, action_kind]
    if source:
        where += " AND source = ?"
        params.append(source)

    with _conn() as conn:
        raw = conn.execute(
            f"SELECT outcome, COUNT(*) c FROM action_events WHERE {where} GROUP BY outcome",
            params,
        ).fetchall()
        rolled = conn.execute(
            f"SELECT SUM(succeeded) s, SUM(failed) f FROM action_event_daily WHERE {where}",
            params,
        ).fetchone()

    counts = {OUTCOME_SUCCEEDED: 0, OUTCOME_FAILED: 0}
    for row in raw:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + int(row["c"])
    counts[OUTCOME_SUCCEEDED] += int((rolled["s"] if rolled else 0) or 0)
    counts[OUTCOME_FAILED] += int((rolled["f"] if rolled else 0) or 0)
    counts["runs"] = counts[OUTCOME_SUCCEEDED] + counts[OUTCOME_FAILED]
    return counts


def compact(
    *, older_than_days: int = RAW_RETENTION_DAYS, max_raw: int = MAX_RAW_EVENTS, now: float | None = None
) -> dict[str, Any]:
    """Roll old raw events into per-day aggregates, then delete the raw rows.

    Counts ranking depends on are carried over exactly; only per-run detail is
    averaged. Compaction is idempotent — running it twice changes nothing.
    """
    init()
    ts = time.time() if now is None else now
    cutoff_day = _day(ts - older_than_days * 86400)

    with _lock, _conn() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM action_events").fetchone()[0])
        over = max(0, total - int(max_raw))
        cutoff_id = 0
        if over:
            row = conn.execute(
                "SELECT id FROM action_events ORDER BY id ASC LIMIT 1 OFFSET ?", (over - 1,)
            ).fetchone()
            cutoff_id = int(row["id"]) if row else 0

        selection = "day < ? OR id <= ?"
        params = (cutoff_day, cutoff_id)

        conn.execute(
            f"""
            INSERT INTO action_event_daily (
              day, goal_family, initiative, band, action_kind, source, runs,
              succeeded, failed, predicates_total, predicates_passed,
              sum_collapse, collapse_n, sum_cost_myr, sum_latency_ms, sum_tokens
            )
            SELECT day, goal_family, initiative, band, action_kind, source,
                   COUNT(*),
                   SUM(CASE WHEN outcome = 'succeeded' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN outcome = 'failed' THEN 1 ELSE 0 END),
                   SUM(predicates_total), SUM(predicates_passed),
                   COALESCE(SUM(collapse), 0), SUM(CASE WHEN collapse IS NULL THEN 0 ELSE 1 END),
                   SUM(cost_myr), SUM(latency_ms), SUM(tokens)
            FROM action_events WHERE {selection}
            GROUP BY day, goal_family, initiative, band, action_kind, source
            ON CONFLICT(day, goal_family, initiative, band, action_kind, source) DO UPDATE SET
              runs = runs + excluded.runs,
              succeeded = succeeded + excluded.succeeded,
              failed = failed + excluded.failed,
              predicates_total = predicates_total + excluded.predicates_total,
              predicates_passed = predicates_passed + excluded.predicates_passed,
              sum_collapse = sum_collapse + excluded.sum_collapse,
              collapse_n = collapse_n + excluded.collapse_n,
              sum_cost_myr = sum_cost_myr + excluded.sum_cost_myr,
              sum_latency_ms = sum_latency_ms + excluded.sum_latency_ms,
              sum_tokens = sum_tokens + excluded.sum_tokens
            """,
            params,
        )
        cur = conn.execute(f"DELETE FROM action_events WHERE {selection}", params)
        compacted = cur.rowcount

    return {"ok": True, "compacted": compacted, "cutoff_day": cutoff_day}


def summary(goal_family: str | None = None) -> dict[str, Any]:
    """Numbers only — safe to show anywhere, contains no user content."""
    init()
    events = list_events(limit=1, goal_family=goal_family)
    with _conn() as conn:
        where = " WHERE goal_family = ?" if goal_family else ""
        params: tuple[Any, ...] = (goal_family,) if goal_family else ()
        raw = conn.execute(
            f"SELECT COUNT(*) c, COALESCE(SUM(cost_myr), 0) cost FROM action_events{where}", params
        ).fetchone()
        rolled = conn.execute(
            f"SELECT COALESCE(SUM(runs), 0) runs, COALESCE(SUM(sum_cost_myr), 0) cost"
            f" FROM action_event_daily{where}",
            params,
        ).fetchone()
        by_initiative = conn.execute(
            f"SELECT initiative, COUNT(*) c FROM action_events{where} GROUP BY initiative", params
        ).fetchall()

    return {
        "raw_events": int(raw["c"]),
        "compacted_runs": int(rolled["runs"]),
        "total_runs": int(raw["c"]) + int(rolled["runs"]),
        "total_cost_myr": round(float(raw["cost"]) + float(rolled["cost"]), 6),
        "by_initiative": {r["initiative"]: int(r["c"]) for r in by_initiative},
        "latest_ts": events[0]["ts"] if events else None,
    }
