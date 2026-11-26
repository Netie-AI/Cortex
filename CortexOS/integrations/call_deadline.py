"""Per-request deadline for OpenVault calls. Off unless CORTEX_CALL_DEADLINE is set.

When the flag is off this module does nothing: timeouts stay as the caller
passed them, ``/health/features`` gains no field, and the chat transport stays
the stdlib client. When it is on, a route may bind ``X-Deadline-Ms`` and a
disconnect signal. ``freeroute.complete`` then uses min(remaining, its own
timeout). A disconnect closes the in-flight httpx call to OpenVault.

The measured pick still uses the task the caller already chose, so a flag-on
ask does not move OpenVault onto a different scoreboard. Calls that today
share ``crew-think`` are counted under ``EXPLICIT_TASKS`` instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any

ENV = "CORTEX_CALL_DEADLINE"
HEADER = "X-Deadline-Ms"

# Purposes that reach FreeRoute as ``crew-think`` because ``task_for`` has no
# row for them (or they pass purpose ``think`` for a different job). The pick
# task is unchanged. ``/health/features`` counts these names.
EXPLICIT_TASKS = {
    "improve": "crew-think-improve",
    "brain": "crew-brain",
    "suggest": "crew-suggest",
}

_deadline: ContextVar[float | None] = ContextVar("cortex_call_deadline", default=None)
_cancel: ContextVar[threading.Event | None] = ContextVar("cortex_call_cancel", default=None)
_label: ContextVar[str | None] = ContextVar("cortex_call_count_label", default=None)

_lock = threading.Lock()
_tasks: dict[str, int] = {}
_abandoned = 0


def enabled() -> bool:
    """True only for ``CORTEX_CALL_DEADLINE`` of 1, true, or yes. Default off."""
    import os

    return (os.environ.get(ENV) or "").strip().lower() in {"1", "true", "yes"}


def parse_ms(raw: str | None) -> float | None:
    """Seconds from a millisecond header. None when it is absent or unusable."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        ms = int(text)
    except ValueError:
        return None
    if ms < 0:
        return None
    return ms / 1000.0


def cap(existing: float) -> float:
    """min(remaining deadline, existing). ``existing`` when the flag is off or unbound."""
    if not enabled():
        return float(existing)
    deadline = _deadline.get()
    if deadline is None:
        return float(existing)
    remaining = deadline - time.monotonic()
    if remaining < 0:
        remaining = 0.0
    return min(float(existing), remaining)


def current_cancel() -> threading.Event | None:
    if not enabled():
        return None
    return _cancel.get()


def cancellable() -> bool:
    """True when a disconnect should close the upstream httpx call."""
    return current_cancel() is not None


def active_label() -> str:
    """Explicit count name for this call, or empty to keep the caller's task."""
    if not enabled():
        return ""
    return (_label.get() or "").strip()


def note(task: str, *, abandoned: bool = False) -> None:
    if not enabled():
        return
    label = (task or "unnamed").strip() or "unnamed"
    global _abandoned
    with _lock:
        _tasks[label] = _tasks.get(label, 0) + 1
        if abandoned:
            _abandoned += 1


def snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "tasks": {key: _tasks[key] for key in sorted(_tasks)},
            "abandoned": _abandoned,
        }


def reset_counts() -> None:
    global _abandoned
    with _lock:
        _tasks.clear()
        _abandoned = 0


@contextmanager
def count_as(label: str) -> Iterator[None]:
    """Count this call under ``label`` when the flag is on. No-op when it is off."""
    if not enabled():
        yield
        return
    token = _label.set(label)
    try:
        yield
    finally:
        _label.reset(token)


@asynccontextmanager
async def bind_http(request: Any) -> AsyncIterator[None]:
    """Bind ``X-Deadline-Ms`` and cancel upstream work if the client leaves.

    A no-op when the flag is off, so the route body runs as it did before.
    """
    if not enabled():
        yield
        return
    seconds = parse_ms(request.headers.get(HEADER))
    event = threading.Event()
    deadline_token = _deadline.set(None if seconds is None else time.monotonic() + seconds)
    cancel_token = _cancel.set(event)

    async def _watch() -> None:
        try:
            while not event.is_set():
                if await request.is_disconnected():
                    event.set()
                    return
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            return

    watcher = asyncio.create_task(_watch())
    try:
        yield
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        _cancel.reset(cancel_token)
        _deadline.reset(deadline_token)


def _note_headers(headers: Mapping[str, str] | None) -> None:
    from CortexOS.integrations.freeroute import note_response_headers

    if not headers:
        note_response_headers({})
        return
    note_response_headers({str(key): str(value) for key, value in headers.items()})


def _json_dict(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def request_httpx(
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 5.0,
    base: str | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """One OpenVault JSON call that closes when the bound client disconnects.

    Same JSON bytes as the stdlib client. Used only while a cancel event is
    bound, so the flag-off path never reaches here.
    """
    import httpx

    from CortexOS.integrations.openvault_client import is_loopback_url, openvault_base_url

    root = base or openvault_base_url()
    url = f"{root}{path}"
    payload = json.dumps(dict(body)).encode("utf-8") if body is not None else None
    sent = {"Accept": "application/json"}
    if payload is not None:
        sent["Content-Type"] = "application/json"
    sent.update(dict(headers or {}))
    cancel = current_cancel()
    client = httpx.Client(timeout=httpx.Timeout(timeout), trust_env=not is_loopback_url(root))
    done = threading.Event()

    def _abort() -> None:
        if cancel is None:
            return
        while not done.is_set():
            if cancel.is_set():
                client.close()
                return
            done.wait(0.05)

    closer = threading.Thread(target=_abort, name="cortex-call-cancel", daemon=True)
    closer.start()
    try:
        try:
            resp = client.request(method.upper(), url, content=payload, headers=sent)
        except httpx.HTTPError:
            return 0, None
        _note_headers(resp.headers)
        return int(resp.status_code), _json_dict(resp.content)
    finally:
        done.set()
        client.close()
        closer.join(timeout=1.0)
