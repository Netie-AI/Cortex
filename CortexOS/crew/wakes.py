"""Long-term calling -- persist timer/completion/event wakes for Crew.

Adapted from OpenWorker WakeStore (read, rewrite, do not vendor). The Crew
process owns the tick. Control never POSTs wakes. Resume is a transcript
message plus on_user_message so HITL floors still apply.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KIND_TIMER = "timer"
KIND_COMPLETION = "completion"
KIND_EVENT = "event"

STATE_PENDING = "pending"
STATE_DUE = "due"
STATE_FIRED = "fired"

SEMANTIC_LAYER_ALIASES = frozenset(
    {
        "catalog",
        "semantic-layer",
        "semantic layer",
        "24/7",
        "insight automation",
    }
)
SEMANTIC_LAYER_WAKE_NOTE = (
    "browse the catalog. Use cortex_ask: what metrics are available in the data."
)


def expand_wake_note(note: str) -> str:
    """Map operator shorthand onto a catalog cortex_ask turn. Other notes pass through."""
    low = (note or "").strip().lower()
    if low in SEMANTIC_LAYER_ALIASES:
        return SEMANTIC_LAYER_WAKE_NOTE
    return (note or "").strip()


def is_semantic_layer_wake(note: str) -> bool:
    """True when a wake (or [wake] transcript line) is the 24/7 catalog turn."""
    raw = (note or "").strip()
    if raw.lower().startswith("[wake] "):
        raw = raw[7:].strip()
    low = raw.lower()
    if low in SEMANTIC_LAYER_ALIASES:
        return True
    return raw == SEMANTIC_LAYER_WAKE_NOTE


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Wake:
    id: str
    space_id: str
    kind: str
    state: str = STATE_PENDING
    fire_at: str | None = None
    job_id: str | None = None
    event_key: str | None = None
    note: str = ""
    created_at: str = field(default_factory=lambda: _now().isoformat(timespec="seconds"))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class WakeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._wakes: dict[str, Wake] = {}
        if self.path.is_file():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for row in raw.get("wakes", []):
                w = Wake(**row)
                self._wakes[w.id] = w

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"wakes": [w.as_dict() for w in self._wakes.values()]}, indent=2),
            encoding="utf-8",
        )

    def add_timer(self, space_id: str, fire_at: datetime, *, note: str = "") -> Wake:
        w = Wake(
            uuid.uuid4().hex[:12],
            space_id,
            KIND_TIMER,
            fire_at=fire_at.isoformat(),
            note=note,
        )
        with self._lock:
            self._wakes[w.id] = w
            self._save()
        return w

    def add_completion(self, space_id: str, job_id: str, *, note: str = "") -> Wake:
        w = Wake(uuid.uuid4().hex[:12], space_id, KIND_COMPLETION, job_id=job_id, note=note)
        with self._lock:
            self._wakes[w.id] = w
            self._save()
        return w

    def add_event(self, space_id: str, event_key: str, *, note: str = "") -> Wake:
        w = Wake(uuid.uuid4().hex[:12], space_id, KIND_EVENT, event_key=event_key, note=note)
        with self._lock:
            self._wakes[w.id] = w
            self._save()
        return w

    def list(self, *, include_fired: bool = False) -> list[Wake]:
        with self._lock:
            rows = list(self._wakes.values())
        if include_fired:
            return rows
        return [w for w in rows if w.state != STATE_FIRED]

    def due(self, now: datetime | None = None) -> list[Wake]:
        now = now or _now()
        out: list[Wake] = []
        with self._lock:
            for w in self._wakes.values():
                if w.state not in (STATE_PENDING, STATE_DUE):
                    continue
                if w.kind == KIND_TIMER and w.fire_at:
                    try:
                        fire = datetime.fromisoformat(w.fire_at)
                    except ValueError:
                        continue
                    if fire.tzinfo is None:
                        fire = fire.replace(tzinfo=timezone.utc)
                    if fire <= now:
                        out.append(w)
                elif w.kind in (KIND_COMPLETION, KIND_EVENT) and w.state == STATE_DUE:
                    out.append(w)
        return out

    def complete_job(self, job_id: str) -> list[Wake]:
        return self._mark_due(lambda w: w.kind == KIND_COMPLETION and w.job_id == job_id)

    def fire_event(self, event_key: str) -> list[Wake]:
        return self._mark_due(lambda w: w.kind == KIND_EVENT and w.event_key == event_key)

    def _mark_due(self, pred: Callable[[Wake], bool]) -> list[Wake]:
        out: list[Wake] = []
        with self._lock:
            for w in self._wakes.values():
                if w.state == STATE_PENDING and pred(w):
                    w.state = STATE_DUE
                    out.append(w)
            if out:
                self._save()
        return out

    def mark_fired(self, wake_id: str) -> Wake | None:
        with self._lock:
            w = self._wakes.get(wake_id)
            if w is None:
                return None
            w.state = STATE_FIRED
            self._save()
            return w
