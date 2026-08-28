"""Gateway: identity + policy before a tool runs."""

from __future__ import annotations

from typing import Any

from night_shift.armor import scan

# Zero-trust-ish: each agent has a role; only placer may commit a PO.
_CAN_PLACE = {"floor.placer.v1"}


def check(*, agent_id: str, tool: str, payload_text: str) -> dict[str, Any]:
    armor = scan(payload_text, tool_name=tool)
    if not armor["ok"]:
        return {"allow": False, "reason": "armor", "armor": armor}
    if tool == "place_order" and agent_id not in _CAN_PLACE:
        return {"allow": False, "reason": "identity_denied", "agent_id": agent_id}
    return {"allow": True, "reason": "ok", "armor": armor}
