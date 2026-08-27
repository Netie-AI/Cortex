"""In-process event bus feeding the /crew/events SSE stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    def __init__(self, max_queue: int = 500):
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._max_queue = max_queue

    def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            if q.qsize() >= self._max_queue:
                # A stalled consumer must not wedge the runtime; it reconnects.
                continue
            q.put_nowait(event)

    async def stream(self) -> AsyncIterator[str]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(q)
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25)
                except (TimeoutError, asyncio.TimeoutError):
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            self._subscribers.discard(q)
