"""Cortex Agent SDK (O4) — the ONE blessed in-process surface for agent runtimes.

Pack-agnostic engine capability: every read routes through ontology visibility
scoping (``agent_visible``), every write routes through the governed F8 action
path (registry allowlist → sanitize → compliance → sandbox → F1 ledger). No
agent reaches data or side effects except through here — actions are the only
write path, identical for human and agent callers.

    from CortexOS.agent_sdk import AgentActor, call_action, query_objects

    actor = AgentActor(actor="agent_stock", role="steward")
    rows = query_objects("inventory", {"category": "Grains"}, actor=actor)
    result = call_action("export_pptx", {"title": "Q3"}, actor=actor, confirmed=True)

The hosted API layer (PARKING_LOT P17) wraps this same surface over the wire;
``packs/<pack>/agents/sdk.py`` may bind it with the pack pre-selected.
"""

from CortexOS.agent_sdk.backends import register_query_backend
from CortexOS.agent_sdk.hooks import clear_agent_hooks, register_agent_hook
from CortexOS.agent_sdk.sdk import (
    AgentActor,
    SdkDenied,
    call_action,
    list_action_types,
    list_functions,
    list_object_types,
    query_objects,
)

__all__ = [
    "AgentActor",
    "SdkDenied",
    "call_action",
    "clear_agent_hooks",
    "list_action_types",
    "list_functions",
    "list_object_types",
    "query_objects",
    "register_agent_hook",
    "register_query_backend",
]
