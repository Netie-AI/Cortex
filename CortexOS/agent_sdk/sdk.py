"""Agent SDK implementation (O4) — reads scoped by the ontology, writes only via actions.

Every function takes ``pack`` (name) or ``pack_dir`` (explicit path — wins when
given) so the engine serves any pack; defaults resolve from the PACK env var.

Read path (``query_objects``): only ``agent_visible`` properties are ever
selected or filterable — for every role. The SDK is the *agent* surface; PII
kept out of prompts is the point, so there is no privileged bypass here.

Write path (``call_action``): registry lookup → role gate → confirm gate →
F8 runner (allowlist → sanitize → compliance → sandbox → F1 ledger). SDK-level
denials are ledgered as ``action.tool_call_denied`` so a refused call is as
auditable as an executed one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from CortexOS.agent_sdk.backends import get_query_backend
from CortexOS.ontology.registry import (
    ActionType,
    FunctionDef,
    ObjectType,
    load_action_types,
    load_functions,
    load_object_types,
    pack_dir_for,
)

_ROLE_RANK = {"viewer": 0, "steward": 1, "admin": 2}


@dataclass(frozen=True, slots=True)
class AgentActor:
    """Minimal identity for SDK calls. api_auth.Caller duck-types (actor, role)."""

    actor: str
    role: str = "viewer"


class SdkDenied(PermissionError):
    """An SDK call was refused. ``verdict`` names the gate that fired."""

    def __init__(self, message: str, *, verdict: str):
        super().__init__(message)
        self.verdict = verdict


def _identity(actor: Any) -> tuple[str, str]:
    name = getattr(actor, "actor", None)
    role = getattr(actor, "role", None)
    if not name or not isinstance(role, str):
        raise SdkDenied(
            "actor must provide .actor and .role (AgentActor or api_auth.Caller)",
            verdict="bad_actor",
        )
    if role not in _ROLE_RANK:
        raise SdkDenied(f"unknown role {role!r}", verdict="bad_actor")
    return str(name), role


def _role_at_least(have: str, need: str) -> bool:
    return _ROLE_RANK.get(have, -1) >= _ROLE_RANK.get(need, 99)


def _resolve_pack_dir(pack: str | None, pack_dir: Path | str | None) -> Path:
    return Path(pack_dir) if pack_dir is not None else pack_dir_for(pack)


# -- discovery ---------------------------------------------------------------

def list_object_types(
    *,
    pack: str | None = None,
    pack_dir: Path | str | None = None,
) -> list[ObjectType]:
    """Object types with agent-INVISIBLE properties stripped from the schema.

    What an agent cannot read it should not even see named — grounding prompts
    on hidden property names invites the model to ask for them.
    """
    out: list[ObjectType] = []
    for obj in load_object_types(_resolve_pack_dir(pack, pack_dir)):
        visible = tuple(p for p in obj.properties if p.agent_visible)
        out.append(
            ObjectType(
                id=obj.id,
                description=obj.description,
                primary_key=obj.primary_key,
                properties=visible,
            )
        )
    return out


def list_action_types(
    *,
    kind: str | None = None,
    pack: str | None = None,
    pack_dir: Path | str | None = None,
) -> list[ActionType]:
    actions = load_action_types(_resolve_pack_dir(pack, pack_dir))
    if kind is not None:
        actions = [a for a in actions if a.kind == kind]
    return actions


def list_functions(
    *,
    pack: str | None = None,
    pack_dir: Path | str | None = None,
) -> list[FunctionDef]:
    return load_functions(_resolve_pack_dir(pack, pack_dir))


# -- reads -------------------------------------------------------------------

def query_objects(
    object_type: str,
    filters: dict[str, Any] | None = None,
    *,
    actor: Any,
    limit: int = 50,
    pack: str | None = None,
    pack_dir: Path | str | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Rows of one object type — agent-visible columns only, equality filters."""
    _identity(actor)  # any known role may read visible data
    resolved = _resolve_pack_dir(pack, pack_dir)
    objects = {o.id: o for o in load_object_types(resolved)}
    if object_type not in objects:
        raise SdkDenied(f"unknown object type {object_type!r}", verdict="not_found")

    visible = [p.name for p in objects[object_type].properties if p.agent_visible]
    hidden = {p.name for p in objects[object_type].properties if not p.agent_visible}
    for key in filters or {}:
        if key in hidden:
            # membership probes against hidden PII are still reads — refuse.
            raise SdkDenied(f"property {key!r} is not agent-visible", verdict="filter_hidden")
        if key not in visible:
            raise SdkDenied(f"unknown property {key!r}", verdict="not_found")

    backend = get_query_backend(pack or ("dms" if pack_dir is None else _missing_pack(resolved)))
    return backend(object_type, visible, dict(filters or {}), max(1, min(int(limit), 500)), db_path=db_path)


def _missing_pack(pack_dir: Path) -> str:
    # explicit pack_dir with no pack name: backends are keyed by pack name
    return pack_dir.name


# -- writes ------------------------------------------------------------------

def call_action(
    action_id: str,
    params: dict[str, Any] | None = None,
    *,
    actor: Any,
    confirmed: bool = False,
    run_id: str | None = None,
    pack: str | None = None,
    pack_dir: Path | str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Invoke a registered ``kind: tool`` action through the governed F8 path."""
    from CortexOS.agent_sdk.hooks import fire

    name, role = _identity(actor)
    actions = {a.id: a for a in load_action_types(_resolve_pack_dir(pack, pack_dir))}

    def _deny(verdict: str, reason: str) -> None:
        _ledger_denial(name, action_id, verdict, reason, run_id=run_id, db_path=db_path)
        fire("on_denied", {"action_id": action_id, "actor": name, "verdict": verdict, "reason": reason})
        raise SdkDenied(reason, verdict=verdict)

    action = actions.get(action_id)
    if action is None:
        _deny("unregistered", f"action {action_id!r} is not a registered action type")
    if action.kind != "tool":
        _deny("not_invocable", f"action {action_id!r} is kind={action.kind!r} — events are recorded, not invoked")
    required = action.required_role or "steward"
    if not _role_at_least(role, required):
        _deny("rbac", f"action {action_id!r} requires role {required!r} (caller={role!r})")
    if action.requires_confirm and not confirmed:
        _deny("confirm_required", f"action {action_id!r} requires human confirmation (pass confirmed=True)")

    from netie.execution.tool_runner import run_tool_call

    fire("before_action", {"action_id": action_id, "actor": name, "role": role, "confirmed": confirmed})
    result = run_tool_call(action_id, dict(params or {}), actor=name, run_id=run_id, db_path=db_path)
    fire("after_action", {"action_id": action_id, "actor": name, "result": result})
    return result


def _ledger_denial(
    actor: str,
    action_id: str,
    verdict: str,
    reason: str,
    *,
    run_id: str | None,
    db_path: Path | str | None,
) -> None:
    from packs.dms.audit import ledger  # shared pack-agnostic ledger spine (F1)

    ledger.append(
        actor,
        "action.tool_call_denied",
        {"tool": action_id, "verdict": verdict, "reason": reason, "run_id": run_id, "path": None},
        db_path=db_path,
    )
