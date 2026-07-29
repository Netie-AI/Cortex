"""Thin preset → existing runner plan (no third orchestrator)."""

from __future__ import annotations

from typing import Any

from CortexOS.execution.architecture_presets import resolve_runner


def plan_for_request(preset: str | None, req: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a RunPlan dict Cortex / AirGPT can hand to dag_runner or compose."""
    plan = resolve_runner(preset)
    body = dict(req or {})
    plan["request"] = {
        "prompt": (body.get("prompt") or body.get("input") or "")[:4000],
        "session_id": body.get("session_id"),
        "project_path": body.get("project_path") or "",
    }
    # Local run still asks OpenVault gate; deploy/leave always gated hard.
    plan["gate_action"] = "deploy" if body.get("intent") == "deploy_to_web" else "run"
    return plan
