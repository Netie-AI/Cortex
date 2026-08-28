"""G2.2 — V(s,a,g): which kind of next step actually pays off for a goal.

**Honesty first.** This is a tabular value estimate with shrinkage, not a
trained world model. There is no JEPA training here and nothing predicts future
observations. It is the MPC-style proxy cost the plan calls for: a table keyed
by `(goal_family, action_kind, source)` holding the mean outcome the engine has
observed, blended against a prior.

The design choice that matters: **the cold fallback is the prior, not a
branch.** With no evidence the value *is* the cosine relevance the seeker
already computed, so ranking is unchanged and the silence litmus cannot break.
As outcomes arrive the estimate slides off the prior at a rate set by
`PRIOR_WEIGHT` — one lucky accept never outranks a well-evidenced action, and a
single bad run never buries one.

    value = (PRIOR_WEIGHT * prior + Σ rewards) / (PRIOR_WEIGHT + n)

Rewards are clamped to [0, 1] so they share a scale with cosine: a proposal the
user acted on scores 1.0, one that failed or was dismissed scores 0.0.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

from CortexOS.paths import data_path

DB_PATH = data_path("engine", "action_value.db")

PRIOR_WEIGHT = 3.0  # ~3 observations before evidence outweighs the prior

# G2.4: outcomes now arrive from two places. A user pressing Accept/Dismiss is a
# statement of intent; a run that merely finished is a weaker hint about the
# same thing. Two rules keep product law 6 ("ranking learns from user decisions
# first") true no matter how much the engine runs on its own:
#   1. an inferred outcome carries a quarter of an explicit one, and
#   2. once any explicit evidence exists, inferred evidence is capped so it can
#      never hold more than half the total weight.
# Together they mean a thousand successful auto-runs cannot overrule the person.
KIND_EXPLICIT = "explicit"
KIND_INFERRED = "inferred"
EXPLICIT_WEIGHT = 1.0
INFERRED_WEIGHT = 0.25

REWARD_ACCEPTED = 1.0  # user turned the proposal into a routine / acted on it
REWARD_SUCCEEDED = 1.0  # the resulting work ran and met its predicates
REWARD_DISMISSED = 0.0  # user explicitly dismissed it
REWARD_FAILED = 0.0  # the resulting work ran and did not meet its predicates

OUTCOME_REWARDS = {
    "accepted": REWARD_ACCEPTED,
    "succeeded": REWARD_SUCCEEDED,
    "dismissed": REWARD_DISMISSED,
    "failed": REWARD_FAILED,
}

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init() -> None:
    with _lock, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS action_values (
              goal_family TEXT NOT NULL,
              action_kind TEXT NOT NULL,
              source TEXT NOT NULL,
              n INTEGER DEFAULT 0,
              total_reward REAL DEFAULT 0,
              wins INTEGER DEFAULT 0,
              updated_at REAL,
              PRIMARY KEY (goal_family, action_kind, source)
            );
            CREATE TABLE IF NOT EXISTS action_outcomes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              goal_family TEXT NOT NULL,
              action_kind TEXT NOT NULL,
              source TEXT NOT NULL,
              outcome TEXT NOT NULL,
              reward REAL NOT NULL,
              proposal_id TEXT DEFAULT '',
              ts REAL
            );
            CREATE INDEX IF NOT EXISTS idx_action_outcomes
              ON action_outcomes(goal_family, action_kind);
            """
        )
        # G2.4 split evidence by origin. Rows written before the split were all
        # user decisions, so they migrate into the explicit columns.
        _ensure_columns(
            conn,
            "action_values",
            {
                "explicit_n": "INTEGER DEFAULT 0",
                "explicit_reward": "REAL DEFAULT 0",
                "inferred_n": "INTEGER DEFAULT 0",
                "inferred_reward": "REAL DEFAULT 0",
            },
        )
        _ensure_columns(conn, "action_outcomes", {"kind": "TEXT DEFAULT 'explicit'"})
        conn.execute(
            "UPDATE action_values SET explicit_n = n, explicit_reward = total_reward"
            " WHERE explicit_n = 0 AND inferred_n = 0 AND n > 0"
        )


def goal_family(goal: dict[str, Any] | str) -> str:
    """Group goals so learning transfers between similar objectives."""
    from CortexOS.execution import scoreboard

    statement = goal if isinstance(goal, str) else str(goal.get("statement") or "")
    return scoreboard.family_id(statement)


def record_outcome(
    family: str,
    action_kind: str,
    source: str,
    outcome: str,
    *,
    proposal_id: str = "",
    reward: float | None = None,
    kind: str = KIND_EXPLICIT,
) -> dict[str, Any]:
    """Teach the table what happened. Unknown outcomes are ignored, not guessed.

    ``kind`` records *who said so* — a user decision (``explicit``, the default)
    or the engine observing its own run (``inferred``).
    """
    init()
    if reward is None:
        if outcome not in OUTCOME_REWARDS:
            return {"ok": False, "error": f"unknown_outcome:{outcome}"}
        reward = OUTCOME_REWARDS[outcome]
    reward = max(0.0, min(1.0, float(reward)))
    kind = KIND_INFERRED if kind == KIND_INFERRED else KIND_EXPLICIT
    inferred = 1 if kind == KIND_INFERRED else 0
    now = time.time()

    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO action_outcomes (goal_family, action_kind, source, outcome, reward,"
            " proposal_id, kind, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (family, action_kind, source, outcome, reward, proposal_id, kind, now),
        )
        conn.execute(
            """
            INSERT INTO action_values (
              goal_family, action_kind, source, n, total_reward, wins,
              explicit_n, explicit_reward, inferred_n, inferred_reward, updated_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(goal_family, action_kind, source) DO UPDATE SET
              n = n + 1,
              total_reward = total_reward + excluded.total_reward,
              wins = wins + excluded.wins,
              explicit_n = explicit_n + excluded.explicit_n,
              explicit_reward = explicit_reward + excluded.explicit_reward,
              inferred_n = inferred_n + excluded.inferred_n,
              inferred_reward = inferred_reward + excluded.inferred_reward,
              updated_at = excluded.updated_at
            """,
            (
                family,
                action_kind,
                source,
                reward,
                1 if reward >= 0.5 else 0,
                0 if inferred else 1,
                0.0 if inferred else reward,
                inferred,
                reward if inferred else 0.0,
                now,
            ),
        )
    return {
        "ok": True,
        "family": family,
        "action_kind": action_kind,
        "reward": reward,
        "kind": kind,
    }


def value(
    family: str,
    action_kind: str,
    source: str,
    *,
    prior: float = 0.0,
    prior_weight: float = PRIOR_WEIGHT,
) -> dict[str, Any]:
    """Shrinkage estimate. With no evidence this returns exactly ``prior``."""
    init()
    with _conn() as conn:
        row = conn.execute(
            "SELECT n, total_reward, explicit_n, explicit_reward, inferred_n, inferred_reward"
            " FROM action_values WHERE goal_family = ? AND action_kind = ? AND source = ?",
            (family, action_kind, source),
        ).fetchone()

    n = int(row["n"]) if row else 0
    total = float(row["total_reward"]) if row else 0.0
    explicit_n = int(row["explicit_n"] or 0) if row else 0
    explicit_reward = float(row["explicit_reward"] or 0.0) if row else 0.0
    inferred_n = int(row["inferred_n"] or 0) if row else 0
    inferred_reward = float(row["inferred_reward"] or 0.0) if row else 0.0

    weight_explicit = explicit_n * EXPLICIT_WEIGHT
    sum_explicit = explicit_reward * EXPLICIT_WEIGHT
    weight_inferred = inferred_n * INFERRED_WEIGHT
    sum_inferred = inferred_reward * INFERRED_WEIGHT

    # Product law 6: once the user has said something, the engine's own
    # observations may match that weight but never exceed it.
    capped = False
    if weight_explicit > 0 and weight_inferred > weight_explicit:
        scale = weight_explicit / weight_inferred
        sum_inferred *= scale
        weight_inferred = weight_explicit
        capped = True

    prior = max(0.0, min(1.0, float(prior)))
    weight = weight_explicit + weight_inferred
    estimate = (prior_weight * prior + sum_explicit + sum_inferred) / (prior_weight + weight)

    return {
        "value": round(estimate, 6),
        "n": n,
        "explicit_n": explicit_n,
        "inferred_n": inferred_n,
        "inferred_capped": capped,
        "prior": round(prior, 6),
        "learned": n > 0,
        "mean_reward": round(total / n, 6) if n else None,
    }


def table(family: str | None = None) -> list[dict[str, Any]]:
    init()
    sql = "SELECT * FROM action_values"
    params: tuple[Any, ...] = ()
    if family:
        sql += " WHERE goal_family = ?"
        params = (family,)
    sql += " ORDER BY n DESC, action_kind"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["mean_reward"] = round(item["total_reward"] / item["n"], 6) if item["n"] else None
        out.append(item)
    return out


def explain(estimate: dict[str, Any]) -> str:
    """One sentence for the UI — never show a bare number with no provenance."""
    if not estimate.get("learned"):
        return "No history yet, so this is ranked on how closely it matches the goal."
    n = estimate["n"]
    mean = estimate.get("mean_reward") or 0.0
    if mean >= 0.7:
        judgement = "usually worth doing"
    elif mean >= 0.4:
        judgement = "sometimes worth doing"
    else:
        judgement = "rarely worth doing"

    explicit = int(estimate.get("explicit_n") or 0)
    inferred = int(estimate.get("inferred_n") or 0)
    if explicit and inferred:
        detail = f"{explicit} from your decisions, {inferred} from runs"
    elif explicit:
        detail = f"{explicit} from your decisions"
    elif inferred:
        detail = f"{inferred} from runs"
    else:
        detail = f"{n} past"
    return f"Ranked from {n} past outcome{'s' if n != 1 else ''} ({detail}) — {judgement}."
