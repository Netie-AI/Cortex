"""
packs/dms/tasks/suggest.py
F4 task suggest — inference-time suggestion engine.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from packs.dms.audit.ledger import default_db_path

BIG_API_PLACEHOLDER = os.getenv("ANTHROPIC_API_KEY", "")


def _sqlite_path() -> str:
    env = os.environ.get("DMS_OPS_DB") or os.environ.get("SQLITE_DB_PATH")
    return env if env else str(default_db_path())


def _rule_candidates(state: dict) -> list[dict]:
    candidates = []
    items = state.get("items", [])
    locations = state.get("locations", [])
    recent_movements = state.get("recent_movements", [])
    compliance_flags = state.get("compliance_flags", [])

    stale_threshold = state.get("stale_days", 30)
    stale_items = [i for i in items if i.get("days_since_movement", 0) >= stale_threshold]
    if stale_items:
        candidates.append({
            "task_id": "audit_stale_items",
            "title": f"Audit {len(stale_items)} stale items",
            "description": f"{len(stale_items)} items have not moved in {stale_threshold}+ days.",
            "action": "audit",
            "targets": [i.get("id") for i in stale_items[:10]],
            "priority": "medium",
            "source": "rule:stale_inventory",
            "confidence": 0.95,
        })

    overloaded = [
        loc for loc in locations
        if loc.get("capacity", 0) > 0
        and (loc.get("occupied", 0) / loc.get("capacity", 1)) > 0.85
    ]
    if overloaded:
        candidates.append({
            "task_id": "rebalance_overloaded",
            "title": f"Rebalance {len(overloaded)} overloaded locations",
            "description": f"{len(overloaded)} bins are over 85% capacity.",
            "action": "move",
            "targets": [loc.get("id") for loc in overloaded[:5]],
            "priority": "high",
            "source": "rule:capacity_overload",
            "confidence": 0.90,
        })

    for flag in compliance_flags:
        candidates.append({
            "task_id": f"resolve_compliance_{flag.get('id', 'unknown')}",
            "title": f"Resolve compliance flag: {flag.get('type', 'unknown')}",
            "description": flag.get("description", "Compliance issue detected."),
            "action": "comply",
            "targets": [flag.get("item_id")],
            "priority": "critical",
            "source": "rule:compliance_flag",
            "confidence": 1.0,
        })

    recent_hour = [m for m in recent_movements if m.get("minutes_ago", 999) <= 60]
    if len(recent_hour) >= 10:
        candidates.append({
            "task_id": "batch_confirm_movements",
            "title": f"Batch-confirm {len(recent_hour)} recent movements",
            "description": f"{len(recent_hour)} movements in the last hour need confirmation.",
            "action": "confirm",
            "targets": [m.get("id") for m in recent_hour],
            "priority": "medium",
            "source": "rule:batch_confirm",
            "confidence": 0.88,
        })

    return candidates


def _score_by_history(candidates: list[dict], history: list[dict]) -> list[dict]:
    accept_rate: dict[str, dict[str, int]] = {}
    for h in history:
        tid = h.get("task_id", "")
        accepted = h.get("accepted", False)
        if tid not in accept_rate:
            accept_rate[tid] = {"accepted": 0, "total": 0}
        accept_rate[tid]["total"] += 1
        if accepted:
            accept_rate[tid]["accepted"] += 1

    for c in candidates:
        tid = c["task_id"]
        if tid in accept_rate and accept_rate[tid]["total"] > 0:
            rate = accept_rate[tid]["accepted"] / accept_rate[tid]["total"]
            c["confidence"] = 0.7 * c["confidence"] + 0.3 * rate
            c["history_accept_rate"] = round(rate, 2)
        else:
            c["history_accept_rate"] = None

    return sorted(candidates, key=lambda x: (-x["confidence"], x["priority"] == "critical"))


def _llm_rank_and_explain(candidates: list[dict], context: str) -> list[dict]:
    if not BIG_API_PLACEHOLDER or len(candidates) <= 3:
        return candidates

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=BIG_API_PLACEHOLDER)
        prompt = f"""
Warehouse context: {context}

Task candidates (JSON):
{json.dumps(candidates[:10], indent=2)}

Re-rank these tasks by operational urgency and add a 1-sentence 'rationale' to each.
Return ONLY a JSON array with the same fields plus 'rationale'. No markdown.
"""
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        ranked = json.loads(raw)
        if isinstance(ranked, list):
            return ranked
    except Exception:
        pass

    return candidates


def _load_history(limit: int = 200) -> list[dict]:
    try:
        with sqlite3.connect(_sqlite_path()) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT task_id, accepted, outcome, ts
                FROM dms_task_choices
                ORDER BY ts DESC
                LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            return [
                {"task_id": r[0], "accepted": bool(r[1]), "outcome": r[2], "ts": r[3]}
                for r in rows
            ]
    except Exception:
        return []


def suggest(state: dict, use_llm: bool = False) -> list[dict]:
    candidates = _rule_candidates(state)
    if not candidates:
        return []

    history = _load_history()
    candidates = _score_by_history(candidates, history)

    if use_llm:
        context_summary = (
            f"Items: {len(state.get('items', []))}, "
            f"Locations: {len(state.get('locations', []))}, "
            f"Recent movements: {len(state.get('recent_movements', []))}"
        )
        candidates = _llm_rank_and_explain(candidates, context_summary)

    for c in candidates:
        c["requires_confirm"] = True
        c["suggested_at"] = datetime.now(timezone.utc).isoformat()

    return candidates[:10]


def record_choice(task_id: str, accepted: bool, actor: str = "user") -> None:
    try:
        with sqlite3.connect(_sqlite_path()) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dms_task_choices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    accepted INTEGER NOT NULL,
                    outcome TEXT,
                    actor TEXT,
                    ts TEXT NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO dms_task_choices (task_id, accepted, actor, ts) VALUES (?, ?, ?, ?)",
                (task_id, int(accepted), actor, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
    except Exception:
        pass


def record_outcome(task_id: str, outcome: str) -> None:
    try:
        with sqlite3.connect(_sqlite_path()) as conn:
            conn.execute("""
                UPDATE dms_task_choices
                SET outcome = ?
                WHERE task_id = ? AND outcome IS NULL
                ORDER BY id DESC
                LIMIT 1
            """, (outcome, task_id))
            conn.commit()
    except Exception:
        pass
