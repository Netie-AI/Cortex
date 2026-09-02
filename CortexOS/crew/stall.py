"""Teammate stall detection -- Gas Town ping idea, no force-kill of Manager.

Rewrite of the deacon stuck-session signals (seq silence, timeout, no ack).
Do not vendor gastown, Beads, or a Mayor daemon. Never cancel the Manager.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from CortexOS.crew.store import CrewStore

MANAGER_NAME = "Manager"


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def detect_stalls(
    store: CrewStore,
    *,
    after_s: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Teammates in thinking/acting whose last authored seq is older than after_s.

    Manager is never returned. after_s is the LLM-timeout analogue (seq age),
    not a Beads heartbeat.
    """
    if after_s <= 0:
        return []
    clock = now or datetime.now(timezone.utc)
    hits: list[dict[str, Any]] = []
    for space in store.list_spaces():
        for agent in store.list_agents(space["id"]):
            if agent.get("name") == MANAGER_NAME:
                continue
            if agent.get("status") not in {"thinking", "acting"}:
                continue
            stamp = _parse_ts(store.last_authored_at(space["id"], agent["id"])) or _parse_ts(
                agent.get("created_at")
            )
            if stamp is None:
                continue
            age = (clock - stamp).total_seconds()
            if age >= after_s:
                hits.append(
                    {
                        "id": agent["id"],
                        "name": agent["name"],
                        "space_id": space["id"],
                        "status": agent["status"],
                        "age_s": int(age),
                        "signal": "seq_age",
                    }
                )
    return hits
