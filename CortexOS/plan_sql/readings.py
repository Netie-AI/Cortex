"""PICK-READING-01: readings a split reconfirm offers, held so the user can pick one.

Founder ruling 2026-10-29 (CLAUDE.md section 9): when two SQL readings
disagree, the reconfirm may offer up to three of them, each a plain-language
description with a row count and no values, and a picked reading runs again
through every gate under the session grant.

A hold is process-local, like the session registry it sits beside. It holds
the model's raw SQL text (so a pick replays the same extract-then-gate path),
the description, the row count and the writer's stamp. It holds **no rows**.
It is read only by its exact id, lives at most :data:`TTL_SECONDS` and never
past the manifest's expiry, and is bound to the session, the Space, the
manifest hash, a digest of the request and the scored pack. Holds are not
per-Space memory.

``CORTEX_PLAN_SQL_READINGS`` (default off) switches on offers and picks.
Stdlib plus :class:`~CortexOS.plan_sql.generator.StepStamp` only.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from CortexOS.plan_sql.generator import StepStamp

READINGS_ENV = "CORTEX_PLAN_SQL_READINGS"
MIN_READINGS = 2
MAX_READINGS = 3
TTL_SECONDS = 600
MAX_HOLDS = 512
#: Three offers of three readings.
MAX_HOLDS_PER_SESSION = 9


def readings_enabled() -> bool:
    """``CORTEX_PLAN_SQL_READINGS``: offer readings on a split, and accept picks. Default off."""
    return (os.environ.get(READINGS_ENV) or "").strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class HeldReading:
    """One offered reading. No rows: a pick re-runs ``model_text`` through every gate."""

    reading_id: str
    model_text: str
    served_sql: str
    statement: str
    row_count: int
    rung: str
    stamp: StepStamp
    index: int
    of: int
    session_id: str
    space_id: str | None
    manifest_hash: str
    request_digest: str
    scored_pack_id: str | None
    #: Epoch seconds: the earlier of now + TTL and the manifest's expiry.
    expires_at: float

    def expires_iso(self) -> str:
        return (
            datetime.fromtimestamp(self.expires_at, tz=timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )


def new_reading_id() -> str:
    return secrets.token_urlsafe(16)


def expiry(manifest_expires_at: Any, *, now: float | None = None) -> float:
    """The earlier of now + :data:`TTL_SECONDS` and the manifest's ``expires_at``.

    A manifest expiry that cannot be read gives no hold time at all.
    """
    start = time.time() if now is None else now
    try:
        text = str(manifest_expires_at).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        manifest_end = parsed.timestamp()
    except (TypeError, ValueError):
        return start
    return min(start + TTL_SECONDS, manifest_end)


def request_digest(question: str, payload_json: Any, schema_context: str | None) -> str:
    """sha256 of the question, the payload and the schema context, as sorted-key compact JSON."""
    canon = json.dumps(
        {"question": question, "payload": payload_json, "schema_context": schema_context},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class ReadingStore:
    """Process-local holds, read only by exact id. ``get`` does not filter expiry:
    the ask path's binding guard does, so its guard-removed proof covers it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holds: OrderedDict[str, HeldReading] = OrderedDict()

    def get(self, reading_id: str) -> HeldReading | None:
        with self._lock:
            return self._holds.get(reading_id)

    def hold(self, held: HeldReading, *, now: float | None = None) -> None:
        clock = time.time() if now is None else now
        with self._lock:
            for key in [k for k, v in self._holds.items() if v.expires_at <= clock]:
                del self._holds[key]
            mine = [k for k, v in self._holds.items() if v.session_id == held.session_id]
            while len(mine) >= MAX_HOLDS_PER_SESSION:
                del self._holds[mine.pop(0)]
            while len(self._holds) >= MAX_HOLDS:
                self._holds.popitem(last=False)
            self._holds[held.reading_id] = held

    def replace(self, held: HeldReading) -> None:
        """Overwrite an existing hold in place (tests)."""
        with self._lock:
            self._holds[held.reading_id] = held

    def clear(self) -> None:
        with self._lock:
            self._holds.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._holds)


_STORE = ReadingStore()


def store() -> ReadingStore:
    return _STORE


def reset_for_tests() -> None:
    _STORE.clear()


__all__ = [
    "MAX_HOLDS",
    "MAX_HOLDS_PER_SESSION",
    "MAX_READINGS",
    "MIN_READINGS",
    "READINGS_ENV",
    "TTL_SECONDS",
    "HeldReading",
    "ReadingStore",
    "expiry",
    "new_reading_id",
    "readings_enabled",
    "request_digest",
    "reset_for_tests",
    "store",
]
