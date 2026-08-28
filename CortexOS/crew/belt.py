"""GitHub Issues conveyor JSON for Control's display proxy.

Same shape as Netie-Crew GET /v1/belt so Cortex Crew can own :8020 (chat UI)
without Control going dark. This module does not POST handoff and does not
decide work shape.
"""

from __future__ import annotations

from typing import Any

from CortexOS.crew.board import snapshot as board_snapshot


def snapshot(*, cortex: dict[str, Any] | None = None) -> dict[str, Any]:
    board = board_snapshot()
    items: list[dict[str, Any]] = []
    for row in board.get("tickets") or []:
        ticket = str(row.get("ticket") or "")
        role = str(row.get("role") or "")
        items.append(
            {
                "repo": row.get("repo") or "",
                "number": ticket,
                "title": ticket,
                "ready": role not in {"SEATED", "HELD"},
                "role": role,
                "url": row.get("owner_pr") or "",
            }
        )
    return {
        "bus": "github-issues",
        "tickets": {"items": items, "unreachable": []},
        "handoffs": [],
        "cortex": cortex if cortex is not None else {"ok": False, "detail": "not probed"},
        "plan_for_next": {
            "kind": "plan",
            "needs_human": True,
            "decides_work_shape": False,
        },
        "converse": True,
        "product": "cortex-crew",
        "law": board.get("law") or "",
    }
