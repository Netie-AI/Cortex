"""Memory hierarchy: session (goldfish) -> vector (recall) -> long-term bank.

Persistence is not memory. A crash dump is persistence. Memory is what you
retrieve on purpose for the next decision.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any


def _tok(text: str) -> set[str]:
    return {t for t in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if len(t) > 1}


def _sim(a: str, b: str) -> float:
    ta, tb = _tok(a), _tok(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / math.sqrt(len(ta) * len(tb))


class MemoryBank:
    def __init__(self) -> None:
        self.session: dict[str, Any] = {}
        self.vectors: list[dict[str, Any]] = []
        self.long_term: list[dict[str, Any]] = []

    def set_session(self, key: str, value: Any) -> None:
        self.session[key] = value

    def get_session(self, key: str, default: Any = None) -> Any:
        return self.session.get(key, default)

    def remember(self, text: str, *, kind: str, vendor: str = "") -> str:
        mid = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        row = {"id": mid, "text": text, "kind": kind, "vendor": vendor}
        self.vectors.append(row)
        if kind in {"vendor_habit", "clerk_preference", "standing_po"}:
            self.long_term.append(row)
        return mid

    def search(self, query: str, *, k: int = 3) -> list[dict[str, Any]]:
        scored = sorted(
            ({**row, "score": round(_sim(query, row["text"]), 4)} for row in self.vectors),
            key=lambda r: r["score"],
            reverse=True,
        )
        return [r for r in scored if r["score"] > 0][:k]

    def dump(self) -> dict[str, Any]:
        return {
            "session_keys": list(self.session),
            "vector_count": len(self.vectors),
            "long_term_count": len(self.long_term),
            "session": self.session,
            "long_term": self.long_term,
        }
