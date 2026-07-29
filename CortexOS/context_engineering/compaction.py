"""Compaction — distill context near the window limit without losing critical state.

Anthropic guidance: maximize recall first, then tighten precision. Safest light
touch = clear stale tool results once the agent has already acted on them.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from .budget import estimate_tokens, fit_text

_TOOL_LINE = re.compile(r"(?i)^(tool\s+\w+|\[tool\s+\w+\]|function_call)\b")


def clear_stale_tool_results(
    blocks: Sequence[str],
    *,
    keep_last: int = 2,
    placeholder: str = "[tool result cleared]",
) -> list[str]:
    """Replace older tool-result blocks with a short stub; keep the newest ones."""
    tool_idxs = [i for i, b in enumerate(blocks) if _looks_like_tool_result(b)]
    if len(tool_idxs) <= keep_last:
        return list(blocks)
    drop = set(tool_idxs[:-keep_last])
    out: list[str] = []
    for i, b in enumerate(blocks):
        if i in drop:
            # Preserve tool name if present for orientation.
            name = _tool_name(b) or "tool"
            out.append(f"Tool {name}: {placeholder}")
        else:
            out.append(b)
    return out


def compact_messages(
    blocks: Sequence[str],
    *,
    token_budget: int,
    keep_tail: int = 4,
    summary_prefix: str = "[compacted earlier context]",
) -> tuple[list[str], bool]:
    """
    Fit message/history blocks into a token budget.

    Strategy:
      1. Clear stale tool results
      2. Keep the last `keep_tail` blocks intact (trim if they alone overflow)
      3. Collapse the head into a single truncated summary line
    """
    cleaned = clear_stale_tool_results(list(blocks))
    if estimate_tokens("\n\n".join(cleaned)) <= token_budget:
        return cleaned, False

    if len(cleaned) <= keep_tail:
        joined, _ = fit_text("\n\n".join(cleaned), token_budget)
        return [joined], True

    head, tail = cleaned[:-keep_tail], cleaned[-keep_tail:]
    # If the tail alone exceeds budget, shrink keep_tail / fit the join.
    while len(tail) > 1 and estimate_tokens("\n\n".join(tail)) > token_budget:
        head = head + [tail[0]]
        tail = tail[1:]
    if estimate_tokens("\n\n".join(tail)) > token_budget:
        fitted, _ = fit_text("\n\n".join(tail), token_budget)
        return [fitted], True

    tail_tokens = estimate_tokens("\n\n".join(tail))
    head_budget = max(64, token_budget - tail_tokens)
    head_summary, _ = fit_text(
        "\n".join(f"- {_one_line(b)}" for b in head),
        head_budget,
        marker="…",
    )
    return [f"{summary_prefix}\n{head_summary}", *tail], True


def summarize_for_reseed(
    blocks: Iterable[str],
    *,
    token_budget: int = 800,
) -> str:
    """High-fidelity-ish seed for a fresh window after compaction (no LLM)."""
    lines: list[str] = []
    for b in blocks:
        s = (b or "").strip()
        if not s:
            continue
        lines.append(f"- {_one_line(s)}")
    text = "Conversation so far:\n" + "\n".join(lines)
    fitted, _ = fit_text(text, token_budget, marker="[summary truncated]")
    return fitted


def _looks_like_tool_result(block: str) -> bool:
    s = (block or "").strip()
    if not s:
        return False
    if s.lower().startswith("tool "):
        return True
    if '"ok":' in s[:200] and ("preview" in s or '"path"' in s or "error" in s):
        return True
    return bool(_TOOL_LINE.match(s.splitlines()[0]))


def _tool_name(block: str) -> str:
    m = re.match(r"(?i)^tool\s+(\w+)", (block or "").strip())
    return m.group(1) if m else ""


def _one_line(text: str, limit: int = 160) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= limit else line[: limit - 1] + "…"
