"""Crew teammate life: spawn/kill/stop, smart-idle, goal/active mode.

Netie-native vocabulary for the operator HUD. Not a second orchestrator and
not a background daemon: parked teammates wait on the existing A2A mailbox
inside the same run task, and mode/goal text live in :mod:`CortexOS.crew.store`
so they survive chat clear.

Status the operator reads: ``active`` | ``idle`` | ``waiting`` | ``goal``.
``failed`` and ``stopped`` are terminal and always carry a visible reason
(R-0011). Legacy ``thinking`` / ``acting`` / ``done`` collapse to that set.
"""

from __future__ import annotations

from typing import Any

MODE_ACTIVE = "active"
MODE_GOAL = "goal"
MODES = frozenset({MODE_ACTIVE, MODE_GOAL})

STATUS_ACTIVE = "active"
STATUS_IDLE = "idle"
STATUS_WAITING = "waiting"
STATUS_GOAL = "goal"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"

STATUSES = frozenset(
    {
        STATUS_ACTIVE,
        STATUS_IDLE,
        STATUS_WAITING,
        STATUS_GOAL,
        STATUS_FAILED,
        STATUS_STOPPED,
    }
)

#: In-loop work. Parked idle/goal is not busy.
WORKING = frozenset({STATUS_ACTIVE, STATUS_WAITING, "thinking", "acting"})

_STATUS_ALIAS = {
    "thinking": STATUS_ACTIVE,
    "acting": STATUS_ACTIVE,
    "done": STATUS_IDLE,
}


def canonical_mode(mode: str | None) -> str:
    text = (mode or MODE_ACTIVE).strip().lower()
    return text if text in MODES else MODE_ACTIVE


def canonical_status(status: str | None) -> str:
    text = (status or STATUS_IDLE).strip().lower()
    text = _STATUS_ALIAS.get(text, text)
    return text if text in STATUSES else STATUS_IDLE


def is_alive(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    if int(row.get("alive") if row.get("alive") is not None else 1) == 0:
        return False
    return canonical_status(str(row.get("status") or "")) != STATUS_STOPPED


def can_accept(row: dict[str, Any] | None) -> bool:
    """False when the operator would see a dead/failed teammate."""
    if not is_alive(row):
        return False
    status = canonical_status(str((row or {}).get("status") or ""))
    return status not in {STATUS_FAILED, STATUS_STOPPED}


def dead_reason(row: dict[str, Any] | None, *, name: str = "agent") -> str:
    who = str((row or {}).get("name") or name).strip() or name
    reason = str((row or {}).get("stop_reason") or "").strip()
    if reason:
        return f"{who} is stopped ({reason})"
    status = canonical_status(str((row or {}).get("status") or ""))
    if status == STATUS_FAILED:
        return f"{who} failed and will not accept work"
    return f"{who} is stopped and will not accept work"


def park_status(row: dict[str, Any] | None) -> str:
    """Idle unless this teammate is in persisted goal mode."""
    mode = canonical_mode(str((row or {}).get("mode") or MODE_ACTIVE))
    return STATUS_GOAL if mode == MODE_GOAL else STATUS_IDLE
