"""Agent Registry - catalog, version, discover. Fleet track requirement."""

from __future__ import annotations

from typing import Any

AGENTS: list[dict[str, Any]] = [
    {
        "id": "clerk.coordinator.v1",
        "name": "Coordinator",
        "role": "routes messy shop-floor text into the Night Shift graph",
        "pattern": "coordinator_dispatch",
        "model": "gemini-3.5-flash",
        "version": 1,
        "department": "purchasing",
    },
    {
        "id": "floor.scout.v1",
        "name": "Scout",
        "role": "extract vendor/sku/qty/week from unstructured chat",
        "pattern": "specialist",
        "model": "gemini-3.5-flash",
        "version": 1,
        "department": "purchasing",
    },
    {
        "id": "floor.stock.v1",
        "name": "Stock",
        "role": "check on-hand vs requested qty",
        "pattern": "parallel_fanout",
        "model": "gemini-3.5-flash",
        "version": 1,
        "department": "warehouse",
    },
    {
        "id": "floor.vendor_memory.v1",
        "name": "VendorMemory",
        "role": "recall last price and last PO for this vendor",
        "pattern": "parallel_fanout",
        "model": "gemini-3.5-flash",
        "version": 1,
        "department": "purchasing",
    },
    {
        "id": "sec.armor.v1",
        "name": "Armor",
        "role": "block injection, PII leak, tool poison",
        "pattern": "parallel_fanout",
        "model": "deterministic",
        "version": 1,
        "department": "security",
    },
    {
        "id": "floor.critic.v1",
        "name": "Critic",
        "role": "loop until the draft PO is safe to place",
        "pattern": "loop_until_pass",
        "model": "gemini-3.5-flash",
        "version": 1,
        "department": "compliance",
    },
    {
        "id": "floor.placer.v1",
        "name": "Placer",
        "role": "HITL + idempotent place-order",
        "pattern": "human_approval",
        "model": "deterministic",
        "version": 1,
        "department": "purchasing",
    },
]


def list_agents(*, department: str | None = None) -> list[dict[str, Any]]:
    if not department:
        return list(AGENTS)
    return [a for a in AGENTS if a["department"] == department]


def get_agent(agent_id: str) -> dict[str, Any] | None:
    for a in AGENTS:
        if a["id"] == agent_id:
            return a
    return None
