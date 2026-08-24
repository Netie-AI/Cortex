"""In-process event bus, one channel per space, fanned out to SSE clients.

Emit never blocks a run: a slow or gone browser gets its queue capped. What is
new here is that a drop is no longer only counted - the affected subscriber is
marked, and its SSE stream sends a ``resync`` event telling that one client to
refetch by ``seq``. The old docstring claimed the UI resynced on reconnect; it
did not, so a browser that fell behind under load silently rendered a
transcript with holes in it and looked perfectly healthy (KB R-0011: a silent
degradation is a lie).

Two things therefore have to hold, and tests/test_crew/test_events.py pins
both: a dropped event always produces a ``resync`` for the subscriber that
lost it, and a subscriber that never drops never sees one.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

QUEUE_MAX = 500


@dataclass(eq=False)  # identity-hashed: two clients are never "the same sub"
class Subscription:
    """One SSE client's queue, plus what it missed while it was behind."""

    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAX)
    )
    lost: int = 0

    def take_loss(self) -> int:
        """Read and clear the pending loss count."""
        lost, self.lost = self.lost, 0
        return lost


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[Subscription]] = defaultdict(set)
        self.dropped: int = 0

    def subscribe(self, space_id: str) -> Subscription:
        sub = Subscription()
        self._subs[space_id].add(sub)
        return sub

    def unsubscribe(self, space_id: str, sub: Subscription) -> None:
        self._subs[space_id].discard(sub)
        if not self._subs.get(space_id):
            self._subs.pop(space_id, None)

    def subscriber_count(self, space_id: str) -> int:
        return len(self._subs.get(space_id, ()))

    def emit(self, space_id: str, event: str, data: dict[str, Any]) -> None:
        for sub in list(self._subs.get(space_id, ())):
            try:
                sub.queue.put_nowait((event, data))
            except asyncio.QueueFull:
                sub.lost += 1
                self.dropped += 1

    @staticmethod
    def sse(event: str, data: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=True)}\n\n"
