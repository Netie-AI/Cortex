"""DMS binding of the engine Agent SDK (O4).

The canonical, pack-agnostic surface lives in ``CortexOS/agent_sdk`` — DMS is a
consumer app on top of the engine (docs/strategy/CORTEX_FINAL_GOAL.md). This
module pre-binds ``pack="dms"`` so DMS-side callers (S1 agents, AirGPT tools in
O5) get the blessed surface with zero ceremony:

    from packs.dms.agents import sdk
    rows = sdk.query_objects("inventory", {"category": "Grains"}, actor=actor)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from CortexOS.agent_sdk import AgentActor, SdkDenied  # re-export for callers
from CortexOS.agent_sdk import sdk as _engine

PACK = "dms"

__all__ = [
    "AgentActor",
    "SdkDenied",
    "call_action",
    "list_action_types",
    "list_functions",
    "list_object_types",
    "query_objects",
]


def list_object_types():
    return _engine.list_object_types(pack=PACK)


def list_action_types(*, kind: str | None = None):
    return _engine.list_action_types(kind=kind, pack=PACK)


def list_functions():
    return _engine.list_functions(pack=PACK)


def query_objects(
    object_type: str,
    filters: dict[str, Any] | None = None,
    *,
    actor: Any,
    limit: int = 50,
    db_path: Path | str | None = None,
    session_id: str | None = None,
    verified: Any | None = None,
):
    return _engine.query_objects(
        object_type,
        filters,
        actor=actor,
        limit=limit,
        pack=PACK,
        db_path=db_path,
        session_id=session_id,
        verified=verified,
    )


def call_action(
    action_id: str,
    params: dict[str, Any] | None = None,
    *,
    actor: Any,
    confirmed: bool = False,
    run_id: str | None = None,
    db_path: Path | str | None = None,
):
    return _engine.call_action(
        action_id,
        params,
        actor=actor,
        confirmed=confirmed,
        run_id=run_id,
        pack=PACK,
        db_path=db_path,
    )
