"""G2.3 — open-set recognition: has the engine ever seen this shape before?

Every ingress path so far has assumed incoming work resembles something the
engine already knows. That assumption is where an agent quietly does the wrong
confident thing. OSR asks the question first and answers in three bands:

| band  | signal                                   | route                        |
|-------|------------------------------------------|------------------------------|
| known | close to a family **that has won before** | that family's stored winner  |
| near  | recognisable but unproven                 | top-3 race (`race_router`)   |
| open  | unlike anything, or a shape never seen    | generate under gen-cFSM      |

Two rules keep this honest:

**Similarity alone never makes something "known".** A family must also have a
proven winner on the scoreboard. Otherwise a brand-new task that merely *sounds*
like an old one would inherit an architecture nobody ever validated for it.

**A never-before-seen payload shape forces `open`, whatever the words say.** A
webhook from a new vendor can use identical vocabulary to a known family while
carrying a completely different structure. Text similarity would happily call
that known; the schema fingerprint refuses to.

Untrusted ingress is classified only *after* wrapping — `classify_external`
rejects unwrapped text outright, so the wrap cannot be skipped by routing
through OSR first.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from typing import Any, Mapping

from CortexOS.execution import untrusted_payload
from CortexOS.paths import data_path

DB_PATH = data_path("engine", "osr.db")

TAU_KNOWN = 0.80
TAU_NEAR = 0.50

BAND_KNOWN = "known"
BAND_NEAR = "near"
BAND_OPEN = "open"

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
            CREATE TABLE IF NOT EXISTS seen_schemas (
              fingerprint TEXT PRIMARY KEY,
              keys TEXT DEFAULT '[]',
              seen_count INTEGER DEFAULT 0,
              first_seen REAL,
              last_seen REAL
            );
            """
        )


# --- payload shape -----------------------------------------------------------


def schema_fingerprint(text: str) -> dict[str, Any]:
    """Structural identity of a payload, independent of its wording.

    Only structured payloads have one — free prose is judged on words alone.
    """
    body = (text or "").strip()
    if not body or body[0] not in "{[":
        return {"structured": False, "fingerprint": None, "keys": []}
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"structured": False, "fingerprint": None, "keys": []}

    if isinstance(parsed, list):
        parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
    if not isinstance(parsed, dict) or not parsed:
        return {"structured": False, "fingerprint": None, "keys": []}

    keys = sorted(str(k) for k in parsed.keys())
    digest = hashlib.sha256("|".join(keys).encode("utf-8")).hexdigest()[:16]
    return {"structured": True, "fingerprint": digest, "keys": keys}


def schema_seen(fingerprint: str | None) -> bool:
    if not fingerprint:
        return False
    init()
    with _conn() as conn:
        row = conn.execute(
            "SELECT seen_count FROM seen_schemas WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
    return bool(row and int(row["seen_count"]) > 0)


def remember_schema(shape: Mapping[str, Any]) -> None:
    """Learn a shape only once it has actually been handled."""
    fingerprint = shape.get("fingerprint")
    if not fingerprint:
        return
    init()
    now = time.time()
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT INTO seen_schemas (fingerprint, keys, seen_count, first_seen, last_seen)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
              seen_count = seen_count + 1, last_seen = excluded.last_seen
            """,
            (fingerprint, json.dumps(list(shape.get("keys") or [])), now, now),
        )


def list_schemas() -> list[dict[str, Any]]:
    init()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM seen_schemas ORDER BY last_seen DESC"
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["keys"] = json.loads(item.get("keys") or "[]")
        except Exception:
            item["keys"] = []
        out.append(item)
    return out


# --- classification ----------------------------------------------------------


def propose_horizon(novelty: float) -> int:
    """More novel work gets more room to think — inside the allowed set."""
    if novelty < 0.5:
        return 3
    if novelty < 0.75:
        return 5
    return 7


def classify(
    text: str,
    *,
    tau_known: float = TAU_KNOWN,
    tau_near: float = TAU_NEAR,
    min_runs: int = 3,
) -> dict[str, Any]:
    """Band an incoming request. Pure read — nothing is learned here."""
    from CortexOS.packaging import require_extra
    from CortexOS.execution import scoreboard

    require_extra("agentic", feature="osr")
    scoreboard.init()
    goal = (text or "").strip()
    shape = schema_fingerprint(goal)
    vector = scoreboard.embed_goal(goal)
    family, similarity = scoreboard.match_family(vector)
    winner = scoreboard.best_preset(family, min_runs=min_runs) if family else None
    novelty = round(max(0.0, min(1.0, 1.0 - float(similarity))), 6)

    new_shape = bool(shape["structured"]) and not schema_seen(shape["fingerprint"])
    assumptions: list[str] = []

    if new_shape:
        band = BAND_OPEN
        assumptions.append(
            "This message is laid out in a way I've never handled before, so I'm treating it "
            "as new even though the wording looks familiar."
        )
    elif similarity >= tau_known and winner is not None:
        band = BAND_KNOWN
        assumptions.append(
            f"I've done this kind of work before and the '{winner['preset']}' approach "
            f"has worked {winner['runs']} time{'s' if winner['runs'] != 1 else ''}, so I'll reuse it."
        )
    elif similarity >= tau_known and winner is None:
        band = BAND_NEAR
        assumptions.append(
            "This looks like work I've seen, but no approach has proven itself on it yet, "
            "so I'll try a few and keep the best."
        )
    elif similarity >= tau_near:
        band = BAND_NEAR
        assumptions.append(
            "This is roughly familiar, so I'll try a few approaches and keep whichever works."
        )
    else:
        band = BAND_OPEN
        assumptions.append(
            "I haven't seen anything like this before, so I'll plan it out step by step "
            "rather than guessing from something older."
        )

    horizon = propose_horizon(novelty) if band == BAND_OPEN else 3
    if band == BAND_OPEN:
        assumptions.append(
            f"I'll allow myself up to {horizon} steps, and check the result against the goal "
            "before calling it done."
        )
    if shape["structured"]:
        assumptions.append(
            f"It arrived as structured data with {len(shape['keys'])} field"
            f"{'s' if len(shape['keys']) != 1 else ''}."
        )

    return {
        "band": band,
        "family_id": family if band != BAND_OPEN else None,
        "matched_family": family,
        "similarity": round(float(similarity), 6),
        "novelty_score": novelty,
        "proposed_horizon": horizon,
        "winner": winner["preset"] if (winner and band == BAND_KNOWN) else None,
        "schema": shape,
        "new_shape": new_shape,
        "assumptions": assumptions,
    }


def classify_external(wrapped_text: str, **kwargs: Any) -> dict[str, Any]:
    """Classify ingress that must already be wrapped as untrusted data.

    Refusing unwrapped input here is what stops OSR from becoming a way to get
    raw external text handled before the wrap is applied.
    """
    if not untrusted_payload.is_wrapped(wrapped_text or ""):
        return {
            "ok": False,
            "error": "payload_not_wrapped",
            "band": None,
            "assumptions": [
                "I won't look at outside text until it's been marked as data rather than "
                "instructions."
            ],
        }

    inner = _inner_payload(wrapped_text)
    out = classify(inner, **kwargs)
    out["ok"] = True
    out["wrapped"] = True
    return out


def _inner_payload(wrapped_text: str) -> str:
    text = wrapped_text or ""
    start = text.find(untrusted_payload.BEGIN)
    end = text.find(untrusted_payload.END)
    if start == -1 or end == -1 or end < start:
        return text
    return text[start + len(untrusted_payload.BEGIN) : end].strip()


# --- routing -----------------------------------------------------------------


def _record_event(
    text: str,
    band_info: Mapping[str, Any],
    outcome: Mapping[str, Any],
    *,
    goal_id: str,
    initiative: str,
) -> None:
    """G2.4 telemetry — numbers and identifiers only, never the payload itself."""
    from CortexOS.execution import action_event, scoreboard

    result = outcome.get("result") or {}
    ok = bool(result.get("ok"))
    try:
        action_event.record(
            initiative=initiative,
            outcome=action_event.OUTCOME_SUCCEEDED if ok else action_event.OUTCOME_FAILED,
            action_kind=str(outcome.get("path") or ""),
            source=f"osr_{band_info.get('band')}",
            goal_id=goal_id,
            goal_family=scoreboard.family_id(text),
            band=str(band_info.get("band") or ""),
            path=str(outcome.get("path") or ""),
            run_id=str(result.get("run_id") or result.get("race_id") or ""),
            collapse=(result.get("cfsm") or {}).get("collapse"),
            latency_ms=int(result.get("latency_ms") or 0),
        )
    except Exception:
        pass  # telemetry must never break the work it is describing


async def route(
    text: str,
    body: Mapping[str, Any] | None = None,
    *,
    predicates: list[dict[str, Any]] | None = None,
    classification: Mapping[str, Any] | None = None,
    learn_shape: bool = True,
    goal_id: str = "",
    initiative: str = "reactive",
) -> dict[str, Any]:
    """Band → execution path. Known reuses, near races, open generates."""
    band_info = dict(classification or classify(text))
    run_body = dict(body or {})
    run_body.setdefault("prompt", text)
    band = band_info.get("band")

    if band == BAND_KNOWN:
        from CortexOS.execution.preset_router import plan_for_request
        from CortexOS.execution.run_plan import execute_run_plan

        preset = str(band_info.get("winner") or "minimal")
        try:
            result = await execute_run_plan(plan_for_request(preset, run_body), run_body)
        except Exception as exc:
            result = {"ok": False, "runner": preset, "error": str(exc)}
        outcome = {"path": "stored_winner", "preset": preset, "result": result}

    elif band == BAND_NEAR:
        from CortexOS.execution import race_router

        outcome = {
            "path": "race",
            "result": await race_router.race(text, run_body, predicates=predicates),
        }

    else:
        from CortexOS.execution import gen_cfsm

        horizon = int(band_info.get("proposed_horizon") or 3)
        horizons = tuple(h for h in gen_cfsm.ALLOWED_HORIZONS if h >= horizon) or (3,)
        outcome = {
            "path": "gen_cfsm",
            "result": await gen_cfsm.iterate_cfsm(
                text, run_body, predicates=predicates, horizons=horizons
            ),
        }

    if learn_shape and band_info.get("schema", {}).get("structured"):
        # Only now — a shape is "seen" once it has actually been handled.
        remember_schema(band_info["schema"])

    _record_event(text, band_info, outcome, goal_id=goal_id, initiative=initiative)

    return {
        "ok": True,
        "band": band,
        "family_id": band_info.get("family_id"),
        "novelty_score": band_info.get("novelty_score"),
        "proposed_horizon": band_info.get("proposed_horizon"),
        "assumptions": band_info.get("assumptions") or [],
        **outcome,
    }
