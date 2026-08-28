"""Attention budget — context is finite; allocate by layer priority."""
from __future__ import annotations

from collections.abc import Mapping

from .layers import LayerId

# Rough chars/token; good enough for routing without tiktoken.
CHARS_PER_TOKEN = 4

# Default share of the total token budget per layer (must sum ≈ 1.0).
# Instructions + tools stay hot; history is the first to compress.
DEFAULT_SHARES: Mapping[LayerId, float] = {
    LayerId.INSTRUCTIONS: 0.18,
    LayerId.TOOLS: 0.12,
    LayerId.EXAMPLES: 0.08,
    LayerId.MEMORY: 0.12,
    LayerId.RETRIEVAL: 0.15,
    LayerId.STATE: 0.08,
    LayerId.MESSAGES: 0.27,
}


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def layer_budgets(total_tokens: int, shares: Mapping[LayerId, float] | None = None) -> dict[LayerId, int]:
    """Split a total token budget across layers. Unused layers keep their share for overflow."""
    s = dict(shares or DEFAULT_SHARES)
    total_share = sum(s.values()) or 1.0
    out: dict[LayerId, int] = {}
    for lid, share in s.items():
        out[lid] = max(0, int(total_tokens * (share / total_share)))
    # Fix rounding drift on MESSAGES (largest, most elastic).
    assigned = sum(out.values())
    if assigned < total_tokens and LayerId.MESSAGES in out:
        out[LayerId.MESSAGES] += total_tokens - assigned
    return out


def fit_text(text: str, token_budget: int, *, marker: str = "[truncated]") -> tuple[str, bool]:
    """Deterministic trim that prefers paragraph/line boundaries."""
    if token_budget <= 0:
        return "", bool(text)
    if estimate_tokens(text) <= token_budget:
        return text, False
    char_limit = max(16, token_budget * CHARS_PER_TOKEN)
    trimmed = text[:char_limit]
    for sep in ("\n\n", "\n", ". ", " "):
        idx = trimmed.rfind(sep)
        if idx > char_limit * 0.6:
            trimmed = trimmed[: idx + len(sep)].rstrip()
            break
    return f"{trimmed}\n{marker}", True
