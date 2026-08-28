"""Subagent contract — final-message-only, spawn-depth gate, output sanitize.

distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md
  Subagents return only final message; fresh context; nesting env-gated;
  final messages sanitized against instruction-shaped injection (v2.1.210+).
"""

from __future__ import annotations

import os
import re
from typing import Any

# Instruction-shaped patterns commonly used in prompt-injection relays.
_INJECTION_PATTERNS = (
    re.compile(r"(?i)\bignore\s+(all\s+)?(previous|prior|above)\s+instructions?\b"),
    re.compile(r"(?i)\byou\s+are\s+now\b.{0,40}\bsystem\b"),
    re.compile(r"(?i)\bSYSTEM\s*:\s*"),
    re.compile(r"(?i)\bdo\s+not\s+follow\s+(your|the)\s+(system|developer)\b"),
    re.compile(r"(?i)```\s*system\b"),
    re.compile(r"(?i)\bnew\s+instructions?\s*:"),
)

DEFAULT_MAX_SPAWN_DEPTH = 0  # 0 = nesting disabled (Claude Code default-off)
ENV_SPAWN_DEPTH = "CORTEX_MAX_SUBAGENT_SPAWN_DEPTH"


def max_spawn_depth() -> int:
    raw = os.environ.get(ENV_SPAWN_DEPTH, "").strip()
    if not raw:
        return DEFAULT_MAX_SPAWN_DEPTH
    try:
        return max(0, min(5, int(raw)))
    except ValueError:
        return DEFAULT_MAX_SPAWN_DEPTH


def assert_spawn_allowed(parent_depth: int = 0) -> int:
    """Return child depth if spawn allowed; raise PermissionError otherwise."""
    cap = max_spawn_depth()
    child = int(parent_depth) + 1
    if cap <= 0:
        raise PermissionError(
            f"subagent nesting disabled (set {ENV_SPAWN_DEPTH}=1..5 to enable); "
            f"attempted spawnDepth={child}"
        )
    if child > cap:
        raise PermissionError(f"subagent spawnDepth {child} exceeds cap {cap}")
    return child


def sanitize_subagent_final(text: str) -> dict[str, Any]:
    """Neutralize instruction-shaped patterns in a subagent final message.

    Returns content safe to relay to the parent/user, plus flags.
    """
    original = text or ""
    cleaned = original
    hits: list[str] = []
    for pat in _INJECTION_PATTERNS:
        if pat.search(cleaned):
            hits.append(pat.pattern)
            cleaned = pat.sub("[neutralized-instruction]", cleaned)
    return {
        "content": cleaned,
        "sanitized": bool(hits),
        "patterns_hit": hits[:8],
        "original_chars": len(original),
        "contract": "final_message_only",
    }


def finalize_subagent_output(raw: dict[str, Any] | str) -> dict[str, Any]:
    """Apply the parent-relay contract: only final text + telemetry, sanitized."""
    if isinstance(raw, str):
        body = sanitize_subagent_final(raw)
        return {"content": body["content"], "subagent": body}
    content = ""
    if isinstance(raw, dict):
        content = str(raw.get("content") or raw.get("final") or "")
    body = sanitize_subagent_final(content)
    out = {
        "content": body["content"],
        "subagent": {
            **{k: v for k, v in body.items() if k != "content"},
            "spawn_depth": (raw or {}).get("spawn_depth") if isinstance(raw, dict) else None,
        },
    }
    if isinstance(raw, dict):
        for key in ("label", "purpose", "phase", "agent", "telemetry", "data"):
            if key in raw:
                out[key] = raw[key]
    return out
