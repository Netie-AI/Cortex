"""Durable work queue for crew work - an item survives a process restart.

A crew run lives in memory: ``CortexOS.crew.runtime`` holds ``_runs`` and
``_space_run``, so a process restart loses everything in flight and nobody is
told what was lost. This module keeps the *queue* on disk instead. Work that
was claimed by a worker which then died is reclaimable, and the retry is
visible in the item's attempt count rather than being replayed silently as if
it were the first try - a silent retry hides a crash loop.

This queue stores and leases. It does not decide work shape and it does not
run anything: ``dag_runner`` + manifest + ledger stay the decision layer, and
nothing here inspects a payload beyond checking that it can be written down.

Backed by a JSONL file - not duckdb, whose import is confined to
``CortexOS/execution/``. Every mutation re-reads the file under a lock and
rewrites it through a temporary file, so two workers (two threads, or two
processes) can never be handed the same item, and a crash mid-write cannot
truncate the queue into a shorter one.

Refusals carry their reason (KB R-0011). Nothing is ever dropped: an item that
runs out of attempts becomes ``dead`` with the reason that killed it, so a
poisoned payload is visible instead of absent.

Absorbed pattern (openworker long-running work), original Cortex code: work is
owned by a lease with a deadline, never by a live object in one process.
"""

from __future__ import annotations

import errno
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

PENDING = "pending"
LEASED = "leased"
DONE = "done"
DEAD = "dead"

DEFAULT_LEASE_S = 60.0
DEFAULT_MAX_ATTEMPTS = 3
LOCK_TIMEOUT_S = 10.0
#: A lock file older than this belonged to a process that died holding it.
#: Breaking it is the same bet the lease makes: a dead holder must not wedge
#: the queue forever.
LOCK_STALE_S = 60.0

_REGISTRY_LOCK = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


class QueueError(RuntimeError):
    """A queue operation was refused. The message says why."""


class QueueCorrupt(QueueError):
    """A queue line could not be parsed.

    Raised rather than skipped: skipping a bad line would delete work that the
    operator still believes is queued.
    """


class UnknownItem(QueueError):
    """No item with that id is in the queue file."""


class LeaseLost(QueueError):
    """The caller no longer holds the lease it is trying to act on.

    Refusing here is what keeps the attempt count honest: a worker whose lease
    was already reaped must not also record a failure, or one lost worker would
    burn two attempts.
    """


@dataclass(frozen=True)
class QueueItem:
    """One unit of stored work. Times are epoch seconds.

    Wall clock, not ``monotonic``: a lease has to still mean something to the
    process that starts after the one holding it died.
    """

    id: str
    space_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = PENDING
    attempts: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    lease_owner: str = ""
    lease_expires_at: float = 0.0
    last_reason: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "space_id": self.space_id,
            "payload": self.payload,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "lease_owner": self.lease_owner,
            "lease_expires_at": self.lease_expires_at,
            "last_reason": self.last_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> QueueItem:
        payload = row.get("payload")
        return cls(
            id=str(row["id"]),
            space_id=str(row.get("space_id") or ""),
            payload=payload if isinstance(payload, dict) else {},
            status=str(row.get("status") or PENDING),
            attempts=int(row.get("attempts") or 0),
            max_attempts=max(1, int(row.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)),
            lease_owner=str(row.get("lease_owner") or ""),
            lease_expires_at=float(row.get("lease_expires_at") or 0.0),
            last_reason=str(row.get("last_reason") or ""),
            created_at=float(row.get("created_at") or 0.0),
            updated_at=float(row.get("updated_at") or 0.0),
        )


class _FileLock:
    """Cross-process mutual exclusion around one queue file.

    ``O_EXCL`` create is the one primitive that behaves the same on Windows and
    POSIX here. A timeout raises instead of blocking forever, because a queue
    that hangs reads to the operator as a crashed worker.
    """

    def __init__(self, path: Path, timeout_s: float) -> None:
        self.path = path
        self.timeout_s = timeout_s
        self._fd: int | None = None

    def __enter__(self) -> _FileLock:
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                self._break_if_stale()
            except OSError as exc:  # pragma: no cover - platform specific
                if exc.errno != errno.EEXIST:
                    raise QueueError(f"cannot lock {self.path.name}: {exc}") from exc
                self._break_if_stale()
            if time.monotonic() >= deadline:
                raise QueueError(
                    f"timed out after {self.timeout_s:g}s waiting for the queue lock"
                    f" {self.path.name}; another worker is holding it"
                )
            time.sleep(0.005)

    def __exit__(self, *_exc: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            os.unlink(str(self.path))
        except FileNotFoundError:
            pass

    def _break_if_stale(self) -> None:
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if age > LOCK_STALE_S:
            try:
                os.unlink(str(self.path))
            except FileNotFoundError:
                pass


def _thread_lock(path: Path) -> threading.Lock:
    """One lock per file path, shared by every queue object in this process.

    The ``O_EXCL`` file lock alone would make two threads of one process spin
    against each other until the timeout, which looks exactly like a deadlock.
    """
    key = str(path)
    with _REGISTRY_LOCK:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


class DurableQueue:
    """A leased, file-backed queue of work items.

    Not a scheduler: it hands out what was pushed, in push order, and takes no
    view on what the payload means.
    """

    def __init__(
        self,
        path: Path,
        *,
        lease_seconds: float = DEFAULT_LEASE_S,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        clock: Callable[[], float] = time.time,
        lock_timeout_s: float = LOCK_TIMEOUT_S,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lease_seconds = float(lease_seconds)
        self.max_attempts = max(1, int(max_attempts))
        self._clock = clock
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        self._lock_timeout_s = float(lock_timeout_s)

    # -- public operations -------------------------------------------------

    def push(
        self,
        space_id: str,
        payload: dict[str, Any] | None = None,
        *,
        item_id: str | None = None,
        max_attempts: int | None = None,
    ) -> QueueItem:
        """Store one item as pending. Returns the stored row."""
        body = dict(payload or {})
        try:
            json.dumps(body)
        except (TypeError, ValueError) as exc:
            raise QueueError(f"payload is not JSON-serialisable: {exc}") from exc
        now = self._clock()
        item = QueueItem(
            id=str(item_id or uuid.uuid4().hex[:12]),
            space_id=str(space_id or ""),
            payload=body,
            status=PENDING,
            max_attempts=max(1, int(max_attempts if max_attempts is not None else self.max_attempts)),
            created_at=now,
            updated_at=now,
        )
        with self._guard():
            items = self._read()
            if any(existing.id == item.id for existing in items):
                raise QueueError(f"item {item.id} is already queued")
            items.append(item)
            self._write(items)
        return item

    def claim(
        self,
        worker: str,
        *,
        space_id: str | None = None,
        lease_seconds: float | None = None,
    ) -> QueueItem | None:
        """Lease the oldest eligible item, or return None when there is none.

        Expired leases are reaped first, so a worker that died does not hold an
        item until somebody remembers to call :meth:`reap`.
        """
        lease = float(lease_seconds if lease_seconds is not None else self.lease_seconds)
        now = self._clock()
        with self._guard():
            items, _reaped = self._expire(self._read(), now)
            picked: QueueItem | None = None
            for index, item in enumerate(items):
                if item.status != PENDING:
                    continue
                if space_id is not None and item.space_id != space_id:
                    continue
                picked = replace(
                    item,
                    status=LEASED,
                    lease_owner=str(worker),
                    lease_expires_at=now + lease,
                    updated_at=now,
                )
                items[index] = picked
                break
            self._write(items)
        return picked

    def heartbeat(
        self, item_id: str, worker: str, *, lease_seconds: float | None = None
    ) -> QueueItem:
        """Extend the lease of an item this worker still holds."""
        lease = float(lease_seconds if lease_seconds is not None else self.lease_seconds)
        now = self._clock()
        with self._guard():
            items, _reaped = self._expire(self._read(), now)
            index, item = self._locate(items, item_id)
            self._require_lease(item, worker, "heartbeat")
            fresh = replace(item, lease_expires_at=now + lease, updated_at=now)
            items[index] = fresh
            self._write(items)
        return fresh

    def complete(self, item_id: str, worker: str, *, reason: str = "completed") -> QueueItem:
        """Mark an item done. Refuses if this worker no longer holds the lease."""
        now = self._clock()
        with self._guard():
            items, _reaped = self._expire(self._read(), now)
            index, item = self._locate(items, item_id)
            self._require_lease(item, worker, "complete")
            fresh = replace(
                item,
                status=DONE,
                lease_owner="",
                lease_expires_at=0.0,
                last_reason=str(reason),
                updated_at=now,
            )
            items[index] = fresh
            self._write(items)
        return fresh

    def fail(self, item_id: str, worker: str, reason: str) -> QueueItem:
        """Record a failed attempt. Back to pending, or dead once attempts run out."""
        now = self._clock()
        with self._guard():
            items, _reaped = self._expire(self._read(), now)
            index, item = self._locate(items, item_id)
            self._require_lease(item, worker, "fail")
            fresh = self._penalise(item, str(reason) or "failed without a reason", now)
            items[index] = fresh
            self._write(items)
        return fresh

    def reap(self) -> list[QueueItem]:
        """Return expired leases to pending (or dead) and report what moved."""
        now = self._clock()
        with self._guard():
            items, reaped = self._expire(self._read(), now)
            if reaped:
                self._write(items)
        return reaped

    def get(self, item_id: str) -> QueueItem | None:
        with self._guard():
            for item in self._read():
                if item.id == item_id:
                    return item
        return None

    def list_items(
        self, *, space_id: str | None = None, status: str | None = None
    ) -> list[QueueItem]:
        with self._guard():
            items = self._read()
        return [
            item
            for item in items
            if (space_id is None or item.space_id == space_id)
            and (status is None or item.status == status)
        ]

    def counts(self, *, space_id: str | None = None) -> dict[str, int]:
        """Status histogram. Dead items are counted, never hidden."""
        out = {PENDING: 0, LEASED: 0, DONE: 0, DEAD: 0}
        for item in self.list_items(space_id=space_id):
            out[item.status] = out.get(item.status, 0) + 1
        return out

    # -- internals ---------------------------------------------------------

    def _guard(self) -> _Guard:
        return _Guard(_thread_lock(self.path), self._lock_path, self._lock_timeout_s)

    def _read(self) -> list[QueueItem]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        items: list[QueueItem] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (TypeError, ValueError) as exc:
                raise QueueCorrupt(
                    f"{self.path.name} line {lineno} is not valid JSON ({exc});"
                    " refusing to read the queue rather than drop queued work"
                ) from exc
            if not isinstance(row, dict) or not row.get("id"):
                raise QueueCorrupt(
                    f"{self.path.name} line {lineno} is not a queue item (no id);"
                    " refusing to read the queue rather than drop queued work"
                )
            items.append(QueueItem.from_dict(row))
        return items

    def _write(self, items: Iterable[QueueItem]) -> None:
        body = "".join(json.dumps(item.as_dict(), sort_keys=True) + "\n" for item in items)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(str(tmp), str(self.path))

    def _locate(self, items: list[QueueItem], item_id: str) -> tuple[int, QueueItem]:
        for index, item in enumerate(items):
            if item.id == item_id:
                return index, item
        raise UnknownItem(f"no queued item {item_id} in {self.path.name}")

    def _require_lease(self, item: QueueItem, worker: str, action: str) -> None:
        if item.status == LEASED and item.lease_owner == str(worker):
            return
        if item.status == LEASED:
            raise LeaseLost(
                f"cannot {action} {item.id}: leased by {item.lease_owner}, not {worker}"
            )
        raise LeaseLost(
            f"cannot {action} {item.id}: it is {item.status}, not leased by {worker}"
            + (f" ({item.last_reason})" if item.last_reason else "")
        )

    def _expire(
        self, items: list[QueueItem], now: float
    ) -> tuple[list[QueueItem], list[QueueItem]]:
        out: list[QueueItem] = []
        reaped: list[QueueItem] = []
        for item in items:
            if item.status != LEASED or item.lease_expires_at > now:
                out.append(item)
                continue
            moved = self._penalise(
                item,
                f"lease held by {item.lease_owner or 'an unnamed worker'} expired",
                now,
            )
            out.append(moved)
            reaped.append(moved)
        return out, reaped

    def _penalise(self, item: QueueItem, reason: str, now: float) -> QueueItem:
        """One place decides retry-or-dead, so a reap and a fail cost the same."""
        attempts = item.attempts + 1
        dead = attempts >= item.max_attempts
        note = f"{reason} (attempt {attempts} of {item.max_attempts})"
        if dead:
            note += "; dead, no attempts left"
        return replace(
            item,
            status=DEAD if dead else PENDING,
            attempts=attempts,
            lease_owner="",
            lease_expires_at=0.0,
            last_reason=note,
            updated_at=now,
        )


class _Guard:
    """Thread lock then file lock, released in the opposite order."""

    def __init__(self, thread_lock: threading.Lock, lock_path: Path, timeout_s: float) -> None:
        self._thread_lock = thread_lock
        self._file_lock = _FileLock(lock_path, timeout_s)

    def __enter__(self) -> _Guard:
        if not self._thread_lock.acquire(timeout=max(1.0, self._file_lock.timeout_s)):
            raise QueueError(
                f"timed out waiting for another thread to release {self._file_lock.path.name}"
            )
        try:
            self._file_lock.__enter__()
        except BaseException:
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self._file_lock.__exit__(*exc)
        finally:
            self._thread_lock.release()


def queue_for(data_dir: Path, name: str = "work") -> DurableQueue:
    """The queue file for one crew data dir, alongside spaces/ and skills/."""
    return DurableQueue(Path(data_dir) / "queue" / f"{name}.jsonl")
