"""Architecture scoreboard + family gate — the racing router's memory.

**Honesty first**, matching ``action_value``: the "family gate" is a
deterministic feature hash, not a learned representation and not JEPA. There is
no training here, nothing predicts a future observation, and similarity is
purely lexical — two goals that mean the same thing in different words land in
unrelated buckets. It is a cheap, stable clustering key that needs no model
download, and it is genuinely useful as that. Calling it JEPA oversold it, in
the same way the README oversold Wasm sandboxing (DOC-01): a name promising a
capability the code does not have.

Real representation learning in this layer is an open architecture decision,
not something this module quietly already does.

Every probe and scaled run lands here (data/engine/scoreboard.db, SQLite WAL,
same pattern as workflow_store). Task families are unbounded: goals embed with
a deterministic 64-dim feature hash (stable across workers and restarts — no
model download), cluster by cosine against running-mean centroids, and share
one architecture binding per family. The F1 audit ledger is deliberately NOT
touched — this is mutable routing telemetry, not compliance evidence; CostLedger
keeps per-node costs and joins by run_id.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from typing import Any

from CortexOS.memory.store import cosine
from CortexOS.paths import data_path

DB_PATH = data_path("engine", "scoreboard.db")
EMBED_DIM = 64

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
            CREATE TABLE IF NOT EXISTS arch_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              task_family TEXT NOT NULL,
              preset TEXT NOT NULL,
              mode TEXT DEFAULT 'probe',
              score REAL DEFAULT 0,
              predicates_pass INTEGER,
              judge_score REAL,
              prompt_tokens INTEGER DEFAULT 0,
              completion_tokens INTEGER DEFAULT 0,
              latency_ms INTEGER DEFAULT 0,
              cost_myr REAL DEFAULT 0,
              ts REAL
            );
            CREATE INDEX IF NOT EXISTS idx_arch_runs_family
              ON arch_runs(task_family, preset);
            CREATE TABLE IF NOT EXISTS arch_families (
              family TEXT PRIMARY KEY,
              centroid TEXT NOT NULL,
              run_count INTEGER DEFAULT 0,
              updated_at REAL
            );
            """
        )


# --- deterministic goal embedding -------------------------------------------


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def embed_goal(text: str) -> list[float]:
    """64-dim feature-hash embedding: unigrams + bigrams, signed buckets, L2."""
    vec = [0.0] * EMBED_DIM
    toks = _tokens(text)
    features: list[tuple[str, float]] = [(t, 1.0) for t in toks]
    features.extend((f"{a} {b}", 0.5) for a, b in zip(toks, toks[1:], strict=False))
    for feat, weight in features:
        digest = hashlib.sha256(feat.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:2], "big") % EMBED_DIM
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vec[bucket] += sign * weight
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def family_id(goal: str) -> str:
    """Deterministic id for a brand-new family: readable slug + short hash."""
    toks = _tokens(goal)[:3] or ["task"]
    digest = hashlib.sha256(" ".join(_tokens(goal)).encode("utf-8")).hexdigest()[:8]
    return "-".join(toks) + "-" + digest


# --- run records + aggregates ------------------------------------------------


def record_run(
    run_id: str,
    task_family: str,
    preset: str,
    *,
    mode: str = "probe",
    score: float = 0.0,
    predicates_pass: bool | None = None,
    judge_score: float | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    cost_myr: float = 0.0,
) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT INTO arch_runs (
              run_id, task_family, preset, mode, score, predicates_pass,
              judge_score, prompt_tokens, completion_tokens, latency_ms,
              cost_myr, ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task_family,
                preset,
                mode,
                float(score),
                None if predicates_pass is None else int(bool(predicates_pass)),
                judge_score,
                int(prompt_tokens),
                int(completion_tokens),
                int(latency_ms),
                float(cost_myr),
                time.time(),
            ),
        )


def family_stats(task_family: str) -> dict[str, dict[str, Any]]:
    """Per-preset aggregates for one family — the router's prior."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT preset,
                   COUNT(*) AS runs,
                   SUM(CASE WHEN mode = 'scaled' THEN 1 ELSE 0 END) AS scaled_runs,
                   AVG(score) AS mean_score,
                   AVG(cost_myr) AS mean_cost_myr,
                   AVG(latency_ms) AS mean_latency_ms,
                   MAX(ts) AS last_ts
            FROM arch_runs WHERE task_family = ?
            GROUP BY preset
            """,
            (task_family,),
        ).fetchall()
    return {
        r["preset"]: {
            "runs": r["runs"],
            "scaled_runs": r["scaled_runs"],
            "mean_score": round(r["mean_score"] or 0.0, 6),
            "mean_cost_myr": round(r["mean_cost_myr"] or 0.0, 6),
            "mean_latency_ms": round(r["mean_latency_ms"] or 0.0, 2),
            "last_ts": r["last_ts"],
        }
        for r in rows
    }


def best_preset(task_family: str, *, min_runs: int = 3) -> dict[str, Any] | None:
    """Stored winner: enough history and a genuinely positive mean score."""
    stats = family_stats(task_family)
    eligible = [
        {"preset": preset, **s}
        for preset, s in stats.items()
        if s["runs"] >= min_runs and s["mean_score"] > 0
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda s: (-s["mean_score"], s["mean_cost_myr"], -s["runs"]))
    return eligible[0]


def list_families() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT family, run_count, updated_at FROM arch_families ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# --- family centroids + confidence gate --------------------------------------


def upsert_family(family: str, vec: list[float]) -> None:
    """Running-mean centroid update — every scaled run teaches its family."""
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT centroid, run_count FROM arch_families WHERE family = ?", (family,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO arch_families (family, centroid, run_count, updated_at) VALUES (?, ?, 1, ?)",
                (family, json.dumps(vec), time.time()),
            )
            return
        old = json.loads(row["centroid"])
        n = int(row["run_count"])
        merged = [(o * n + v) / (n + 1) for o, v in zip(old, vec, strict=False)]
        conn.execute(
            "UPDATE arch_families SET centroid = ?, run_count = ?, updated_at = ? WHERE family = ?",
            (json.dumps(merged), n + 1, time.time(), family),
        )


def match_family(vec: list[float]) -> tuple[str | None, float]:
    """Nearest family centroid by cosine over the feature hash (not a learned model)."""
    best_family: str | None = None
    best_sim = 0.0
    with _conn() as conn:
        rows = conn.execute("SELECT family, centroid FROM arch_families").fetchall()
    for row in rows:
        sim = cosine(vec, json.loads(row["centroid"]))
        if sim > best_sim:
            best_sim = sim
            best_family = row["family"]
    return best_family, round(best_sim, 6)


def should_race(
    goal: str, *, sim_threshold: float = 0.80, min_runs: int = 3
) -> dict[str, Any]:
    """The gate: confident family match with a stored winner → skip the race."""
    vec = embed_goal(goal)
    family, sim = match_family(vec)
    if family is not None and sim >= sim_threshold:
        winner = best_preset(family, min_runs=min_runs)
        if winner is not None:
            return {
                "race": False,
                "family": family,
                "similarity": sim,
                "winner": winner["preset"],
                "reason": "family_confident",
                "vector": vec,
            }
        return {
            "race": True,
            "family": family,
            "similarity": sim,
            "winner": None,
            "reason": "insufficient_history",
            "vector": vec,
        }
    return {
        "race": True,
        "family": None,
        "similarity": sim,
        "winner": None,
        "reason": "no_family_match",
        "vector": vec,
    }
