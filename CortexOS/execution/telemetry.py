"""Lightweight submit / ask run telemetry (C4-min).

Durable ``query_run`` persistence is C8. This module keeps an in-process ring
of recent runs and emits structured log lines with never-secret fields.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

_LOG = logging.getLogger("cortex.execution.submit")
_LOCK = threading.Lock()
_RECENT: deque[dict[str, Any]] = deque(maxlen=500)


@dataclass(slots=True)
class RunRecord:
    run_id: str
    session_id: str | None
    pool_id: str | None
    kind: str
    status: str
    queue_ms: float | None
    exec_ms: float | None
    row_count: int | None
    issuer_kid: str | None
    error: str | None
    recorded_at: str


def new_run_id() -> str:
    return str(uuid.uuid4())


def record_run(
    *,
    run_id: str,
    kind: str,
    status: str,
    session_id: str | None = None,
    pool_id: str | None = None,
    queue_ms: float | None = None,
    exec_ms: float | None = None,
    row_count: int | None = None,
    issuer_kid: str | None = None,
    error: str | None = None,
) -> RunRecord:
    rec = RunRecord(
        run_id=run_id,
        session_id=session_id,
        pool_id=pool_id,
        kind=kind,
        status=status,
        queue_ms=queue_ms,
        exec_ms=exec_ms,
        row_count=row_count,
        issuer_kid=issuer_kid,
        error=error,
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )
    payload = asdict(rec)
    with _LOCK:
        _RECENT.appendleft(payload)
    _LOG.info(
        "submit_run run_id=%s kind=%s status=%s session_id=%s pool_id=%s "
        "queue_ms=%s exec_ms=%s row_count=%s issuer_kid=%s error=%s",
        run_id,
        kind,
        status,
        session_id,
        pool_id,
        queue_ms,
        exec_ms,
        row_count,
        issuer_kid,
        error,
    )
    return rec


def recent_runs(limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        return list(_RECENT)[: max(0, limit)]


def clear_runs_for_tests() -> None:
    with _LOCK:
        _RECENT.clear()


__all__ = ["RunRecord", "clear_runs_for_tests", "new_run_id", "recent_runs", "record_run"]
