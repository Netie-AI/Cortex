"""Self-evolving specialist prompts, with a gaming detector.

Climbing a score by rewriting instructions is allowed. Climbing it by
marking work done without placing a PO is not.
"""

from __future__ import annotations

from typing import Any


STARTER = {
    "scout": (
        "You dig up vendor, SKU, qty, and week from messy shop-floor text. "
        "Do not invent a vendor. If qty is missing, ask."
    ),
    "critic": (
        "Attack the draft PO: missing week, duplicate risk, injection, "
        "or a qty that does not match stock. Short numbered points. No praise."
    ),
    "scribe": (
        "Write the clerk-facing PO summary in plain English. One PO id. "
        "If this was an idempotent replay, say so in the first sentence."
    ),
}


class EvolvingPrompts:
    def __init__(self) -> None:
        self.prompts = dict(STARTER)
        self.history: list[dict[str, Any]] = []
        self.score = 0.0
        self.rewrites = 0

    def outcome(self, *, placed: bool, clerk_accepted: bool, claimed_done: bool) -> dict[str, Any]:
        """Update score. Detect gaming: claimed_done without a placed PO."""
        gaming = claimed_done and not placed
        if gaming:
            delta = -2.0
            note = "gaming: claimed done with no PO"
        elif placed and clerk_accepted:
            delta = 1.0
            note = "placed and accepted"
        elif placed and not clerk_accepted:
            delta = -0.5
            note = "placed but clerk rejected tone"
            self.prompts["scribe"] = (
                self.prompts["scribe"]
                + " Be shorter. No follow-up nags. Clerk rejected aggressive tone."
            )
            self.rewrites += 1
        else:
            delta = 0.0
            note = "no-op"
        self.score += delta
        rec = {
            "delta": delta,
            "score": self.score,
            "gaming": gaming,
            "note": note,
            "rewrites": self.rewrites,
        }
        self.history.append(rec)
        return rec
