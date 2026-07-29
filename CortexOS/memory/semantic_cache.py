"""Small embedding-similarity cache for repeated memory answers."""

from __future__ import annotations

from dataclasses import dataclass

from netie.memory.store import cosine


@dataclass(slots=True)
class _CacheEntry:
    vector: list[float]
    answer: object


class SemanticCache:
    """In-memory answer cache matched by cosine similarity."""

    def __init__(self, threshold: float = 0.92) -> None:
        if not -1.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between -1.0 and 1.0")
        self.threshold = threshold
        self._entries: list[_CacheEntry] = []
        self.hit_count = 0

    def get(self, vector: list[float]) -> object | None:
        """Return the first answer whose vector meets the similarity threshold."""
        for entry in self._entries:
            if cosine(vector, entry.vector) >= self.threshold:
                self.hit_count += 1
                return entry.answer
        return None

    def put(self, vector: list[float], answer: object) -> None:
        """Store an answer for a vector, replacing an equivalent cache key."""
        for entry in self._entries:
            if cosine(vector, entry.vector) >= self.threshold:
                entry.vector = list(vector)
                entry.answer = answer
                return
        self._entries.append(_CacheEntry(vector=list(vector), answer=answer))
