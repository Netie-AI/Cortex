"""S0 — per-stream in-memory buffer + batch writer into the bronze lakehouse.

At-least-once with per-batch dedup by `event_id`. A hard cap gives backpressure
(the API turns BackpressureError into HTTP 429). Writes are batched because
DuckLake commits are cheap in bulk and costly per-row.

Concurrency: producers hold ``_BUF_LOCK`` only briefly (append / drain the
buffer). The expensive DuckLake write happens outside that lock, under a separate
``_WRITE_LOCK``, reusing one cached writer connection — so many producer threads
don't serialize on catalog attach + insert. (Stress-tuned: the naive
connect-per-flush-under-buffer-lock design collapsed to ~25 ev/s @ 8 threads.)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import threading
from typing import Any

from packs.dms.lakehouse.catalog import LAKE_ALIAS, connect

_BUFFERS: dict[str, list[dict]] = {}
_BUF_LOCK = threading.RLock()
_WRITE_LOCK = threading.Lock()


class BackpressureError(RuntimeError):
    """Raised when a stream's buffer exceeds the hard cap (writer can't keep up)."""


def _batch_size() -> int:
    return int(os.environ.get("DMS_STREAM_BATCH", "500"))


def _hard_cap() -> int:
    return int(os.environ.get("DMS_STREAM_HARD_CAP", "10000"))


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _table(stream_id: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "_", stream_id.lower()).strip("_") or "stream"
    return f"stream_{safe}"


def _event_id(ev: dict) -> str:
    if ev.get("event_id"):
        return str(ev["event_id"])
    return hashlib.sha256(json.dumps(ev, sort_keys=True, default=str).encode()).hexdigest()[:32]


def reset_writer() -> None:
    """No-op kept for API compatibility. Writes use a fresh per-batch connection
    (DuckDB connections are not safe to share across threads), so there is no
    cached writer to reset."""
    return None


def append_events(stream_id: str, events: list[dict], *, auto_flush: bool = True) -> dict[str, Any]:
    """Buffer events; auto-flush when the batch threshold is reached.

    Returns {accepted, buffered, flushed}. Raises BackpressureError if the buffer
    is over the hard cap (the writer is falling behind)."""
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    now = _now()
    normalized = []
    for ev in events:
        if not isinstance(ev, dict):
            ev = {"value": ev}
        normalized.append({
            "event_id": _event_id(ev),
            "ts": str(ev.get("ts") or now),
            "payload": json.dumps(ev, default=str),
        })

    to_write: list[dict] | None = None
    with _BUF_LOCK:
        buf = _BUFFERS.setdefault(stream_id, [])
        if len(buf) + len(normalized) > _hard_cap():
            raise BackpressureError(
                f"stream {stream_id!r} buffer over cap ({_hard_cap()}); retry later")
        buf.extend(normalized)
        buffered = len(buf)
        if auto_flush and len(buf) >= _batch_size():
            to_write = _drain_locked(stream_id)
            buffered = 0

    flushed = _write(stream_id, to_write) if to_write else 0
    return {"accepted": len(normalized), "buffered": buffered, "flushed": flushed}


def flush(stream_id: str) -> int:
    with _BUF_LOCK:
        batch = _drain_locked(stream_id)
    return _write(stream_id, batch)


def _drain_locked(stream_id: str) -> list[dict]:
    """Pop + per-batch dedup the buffer (must hold _BUF_LOCK)."""
    buf = _BUFFERS.get(stream_id) or []
    if not buf:
        return []
    seen: set[str] = set()
    batch: list[dict] = []
    for row in buf:
        if row["event_id"] in seen:
            continue
        seen.add(row["event_id"])
        batch.append(row)
    _BUFFERS[stream_id] = []
    return batch


def _write(stream_id: str, batch: list[dict]) -> int:
    if not batch:
        return 0
    # Writes serialize (single DuckLake catalog uses optimistic concurrency), but
    # each uses its OWN connection used by exactly one thread — never shared.
    with _WRITE_LOCK:
        con = connect()
        try:
            _write_batch(con, stream_id, batch)
        finally:
            con.close()
    _audit("stream.flushed", {"stream_id": stream_id, "count": len(batch)})
    return len(batch)


def _write_batch(con, stream_id: str, batch: list[dict]) -> None:
    table = f"{LAKE_ALIAS}.bronze.{_table(stream_id)}"
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {LAKE_ALIAS}.bronze")
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {table} ("
        " event_id VARCHAR, ts VARCHAR, payload VARCHAR,"
        " _stream_id VARCHAR, _received_at VARCHAR)"
    )
    now = _now()
    rows = [[r["event_id"], r["ts"], r["payload"], stream_id, now] for r in batch]
    # ONE multi-row INSERT per chunk — executemany runs N separate DuckLake commits
    # (~40ms/row); a single VALUES statement is one write (~0.4ms/row), a 100x win.
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        placeholders = ",".join(["(?,?,?,?,?)"] * len(chunk))
        flat = [v for row in chunk for v in row]
        con.execute(f"INSERT INTO {table} VALUES {placeholders}", flat)


def buffer_depth(stream_id: str) -> int:
    with _BUF_LOCK:
        return len(_BUFFERS.get(stream_id, []))


def _audit(event: str, payload: dict) -> None:
    try:
        from packs.dms.audit import ledger

        ledger.append("system", event, payload)
    except Exception:  # noqa: BLE001
        pass
