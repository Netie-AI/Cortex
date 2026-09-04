"""Crew-owned wake ticks. Control GET-displays them; Control never POSTs.

This is not a second A2A mailbox. Mailbox cursors stay on
:class:`CortexOS.crew.a2a.Mailbox` (``drain(after_seq=...)``). A wake is a
named tick the operator can see on the belt: timer, confirm, mailbox-nonempty
as a *derived* flag, never a parallel inbox.

Crew owns the tick. Engine Crew serves ``GET /crew/wakes`` and the belt
snapshot. Hung converse on :8020 is not restarted from here (R-0015). Belt
does not HTTP-ping Cortex; ``cortex.detail`` is ``not probed``.
"""

from __future__ import annotations

import uuid
from typing import Any

from CortexOS.crew.queue import JobQueue
from CortexOS.crew.store import CrewStore


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class WakeBoard:
    """In-process tick list for one CrewApp. Not durable across process death.

    Persistence of work is the transcript and the job queue. A wake is a
    signal that something is waiting *now*. Restarting Crew rebuilds derived
    ticks from store/mailbox rather than replaying a lost in-memory list.
    """

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def arm(self, kind: str, note: str, *, state: str = "pending") -> dict[str, Any]:
        kind_s = (kind or "timer").strip() or "timer"
        note_s = (note or "").strip()
        row = {
            "id": _new_id(),
            "kind": kind_s,
            "state": (state or "pending").strip() or "pending",
            "note": note_s,
        }
        self._items.append(row)
        return dict(row)

    def list(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._items]

    def public(self) -> list[dict[str, Any]]:
        return [
            {"kind": row["kind"], "state": row["state"], "note": row["note"]}
            for row in self._items
        ]

    def snapshot(self) -> dict[str, Any]:
        """JSON for ``GET /crew/wakes``. Talk liveness is 200 + this body."""
        return {"ok": True, "wakes": self.public()}

    def clear(self) -> None:
        self._items.clear()


def _ticket_item(row: dict[str, Any]) -> dict[str, Any]:
    ticket = str(row.get("ticket") or "").strip()
    repo = str(row.get("repo") or "").strip()
    number: int | None = None
    if "#" in ticket:
        _left, right = ticket.rsplit("#", 1)
        if right.isdigit():
            number = int(right)
    return {
        "repo": repo,
        "number": number,
        "title": ticket or str(row.get("owner_pr") or ""),
        "ready": str(row.get("role") or "") != "SEATED",
    }


def conveyor(
    store: CrewStore,
    wakes: WakeBoard,
    queue: JobQueue,
    *,
    mailbox_nonempty: bool = False,
) -> dict[str, Any]:
    """Display JSON for ``GET /v1/belt`` and ``GET /crew/belt``.

    Control proxies this as display-only. Crew owns leases and the tick.
    ``plan_for_next.decides_work_shape`` stays false: this snapshot does not
    pick the next writer. Cortex is not probed from the belt.
    """
    from CortexOS.crew.board import snapshot as board_snapshot

    board = board_snapshot()
    items = [_ticket_item(t) for t in board.get("tickets") or [] if isinstance(t, dict)]
    spaces = store.list_spaces()
    confirms: list[dict[str, Any]] = []
    agents: list[dict[str, Any]] = []
    for space in spaces:
        confirms.extend(store.pending_confirms(space["id"]))
        agents.extend(store.list_agents(space["id"]))
    public_wakes = wakes.public()
    if mailbox_nonempty and not any(w.get("kind") == "mailbox" for w in public_wakes):
        public_wakes = [
            *public_wakes,
            {"kind": "mailbox", "state": "pending", "note": "mailbox nonempty"},
        ]
    return {
        "bus": "github-issues",
        "tickets": {"items": items, "unreachable": []},
        "handoffs": [],
        "cortex": {"ok": False, "detail": "not probed"},
        "plan_for_next": {"decides_work_shape": False, "needs_human": True},
        "wakes": public_wakes,
        "queue": queue.counts(),
        "confirms": [{"id": c["id"]} for c in confirms],
        "spaces": [{"id": s["id"]} for s in spaces],
        "agents": [{"id": a["id"], "name": a["name"]} for a in agents],
        "converse": True,
    }
