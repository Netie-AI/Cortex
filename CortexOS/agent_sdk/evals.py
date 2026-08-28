"""Agent-definition evals (P16) — the containment gate before any agent ships.

An agent definition (AirGPT ``agent_store`` row, O6 builder output, or hand-written
YAML) declares the tools / object types / action types it may touch. This module
verifies that declaration against the pack's ontology registry BEFORE the agent
runs: every reference must exist, and invocable references must actually be
invocable. Palantir's rule, enforced locally: an agent cannot be granted a tool
that reaches further than the ontology allows.

REQUIRED gate for O6 (agent builder) per PARKING_LOT P16 — a builder without an
evals harness ships unverifiable agents.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from CortexOS.ontology.registry import load_action_types, load_object_types, pack_dir_for


@dataclass(frozen=True, slots=True)
class ScopeReport:
    """Outcome of one agent-definition eval. ``ok`` is False on any violation."""

    agent: str
    ok: bool
    violations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()      # non-fatal observations (e.g. confirm-gated tools)


def check_agent_scope(
    agent_def: dict[str, Any],
    *,
    pack: str | None = None,
    pack_dir: Path | str | None = None,
    runtime_tools: Iterable[str] = (),
) -> ScopeReport:
    """Verify one agent definition against the ontology registry.

    ``runtime_tools`` names the host runtime's own tool surface (e.g. AirGPT's
    TOOLS dict keys) — agent ``tools`` entries must come from that surface or be
    registered ``kind: tool`` action types. Everything else is a violation.
    """
    resolved = Path(pack_dir) if pack_dir is not None else pack_dir_for(pack)
    name = str(agent_def.get("name") or agent_def.get("id") or "<unnamed>")
    violations: list[str] = []
    notes: list[str] = []

    actions = {a.id: a for a in load_action_types(resolved)}
    objects = {o.id for o in load_object_types(resolved)}
    tool_actions = {a.id for a in actions.values() if a.kind == "tool"}
    runtime = set(runtime_tools)

    for obj in agent_def.get("allowed_object_types") or []:
        if obj not in objects:
            violations.append(f"object type {obj!r} not in registry")

    for act in agent_def.get("allowed_action_types") or []:
        a = actions.get(act)
        if a is None:
            violations.append(f"action type {act!r} not in registry")
        elif a.kind != "tool":
            violations.append(f"action type {act!r} is kind={a.kind!r} — recorded, never invocable")
        elif a.requires_confirm:
            notes.append(f"action {act!r} requires human confirmation per registry")

    for tool in agent_def.get("tools") or []:
        if tool in runtime or tool in tool_actions:
            continue
        violations.append(f"tool {tool!r} is neither a runtime tool nor a registered action type")

    return ScopeReport(agent=name, ok=not violations, violations=tuple(violations), notes=tuple(notes))


def evaluate_agents(
    agent_defs: Iterable[dict[str, Any]],
    *,
    pack: str | None = None,
    pack_dir: Path | str | None = None,
    runtime_tools: Iterable[str] = (),
) -> list[ScopeReport]:
    """Eval a whole agent store; returns one report per agent, failures first."""
    reports = [
        check_agent_scope(d, pack=pack, pack_dir=pack_dir, runtime_tools=runtime_tools)
        for d in agent_defs
    ]
    return sorted(reports, key=lambda r: r.ok)
