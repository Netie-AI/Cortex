"""Agent lifecycle hooks (P16) — observe the SDK write path.

Register observers fired around ``call_action``: ``before_action``,
``after_action``, ``on_denied`` (the Cursor beforeSubmitPrompt/afterAgentResponse
pattern, engine-side). Observers are fire-and-forget: a raising hook is swallowed
so a bad observer can never break governance or the write path. Blocking/veto
hooks are a deliberate future extension — today the allowlist + RBAC + confirm
gates are the blockers; hooks are observability + defense-in-depth.

A built-in ``after_action`` scanner flags any secret pattern that appears in a
returned result (output-secrets scanner, P16) on top of the runner's param
sanitize — belt and braces for tools that return fetched/derived content.

Permission pipeline order (distill Claude Code v2.1.212 — do not reorder):
  hooks → deny → ask(confirm) → mode → allow → callback
Netie today: hooks(observe) → registry deny → RBAC deny → confirm_required(ask)
→ F8 allowlist/sanitize/compliance → ledger. Deny remains un-bypassable.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

EVENTS = ("before_action", "after_action", "on_denied")
Hook = Callable[[dict], None]

# Documented evaluation order for hosts / future veto hooks.
PERMISSION_PIPELINE = (
    "hooks",
    "deny",
    "ask",
    "mode",
    "allow",
    "callback",
)

_HOOKS: dict[str, list[Hook]] = {e: [] for e in EVENTS}


def register_agent_hook(event: str, fn: Hook) -> None:
    if event not in _HOOKS:
        raise ValueError(f"unknown hook event {event!r}; choose from {EVENTS}")
    _HOOKS[event].append(fn)


def clear_agent_hooks(event: str | None = None) -> None:
    """Drop user hooks (keeps built-ins). Test/host hygiene between runs."""
    for e in ([event] if event else EVENTS):
        _HOOKS[e] = [h for h in _HOOKS[e] if getattr(h, "_builtin", False)]


def fire(event: str, ctx: dict[str, Any]) -> None:
    for fn in _HOOKS.get(event, ()):
        try:
            fn(ctx)
        except Exception:  # observers must never break the write path
            pass


def _scan_output_secrets(ctx: dict[str, Any]) -> None:
    result = ctx.get("result")
    if not isinstance(result, dict):
        return
    try:
        from scripts.secrets_scan import scan_text
    except Exception:
        return
    findings = scan_text(json.dumps(result, default=str))
    if findings:
        result["secrets_flagged"] = sorted({name for name, _ in findings})


_scan_output_secrets._builtin = True  # type: ignore[attr-defined]
register_agent_hook("after_action", _scan_output_secrets)
