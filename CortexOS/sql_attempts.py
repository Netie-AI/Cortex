"""The SQL one request tried, and the error text it produced. Pure bookkeeping.

The response boundary (:mod:`CortexOS.api.response_scrub`) needs to know which
names a request's SQL attempts referenced, including attempts the gate or the
executor refused and that never reach the envelope as SQL. The SQL gate and
the executor note each statement here; a route opens a :func:`recording` for
the request and reads it back at its exits.

With no recording open, :func:`note_sql` and :func:`note_error` do nothing.
The record is a context variable, so concurrent requests never see each
other's attempts. This module has no imports beyond the standard library on
purpose: the gate and the executor both import it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

#: Bounds on what one request can make the record hold.
MAX_ENTRIES = 64
MAX_CHARS = 50_000


@dataclass(slots=True)
class AttemptRecord:
    """Statements this request handed to the gate or the executor, and their errors."""

    sql: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


_CURRENT: ContextVar[AttemptRecord | None] = ContextVar("cortex_sql_attempts", default=None)


def _add(bucket: list[str], text: object) -> None:
    value = str(text or "")
    if value.strip() and len(bucket) < MAX_ENTRIES:
        bucket.append(value[:MAX_CHARS])


def note_sql(sql: object) -> None:
    """Record one statement this request tried. No-op outside a recording."""
    record = _CURRENT.get()
    if record is not None:
        _add(record.sql, sql)


def note_error(text: object) -> None:
    """Record error text produced for one of this request's statements."""
    record = _CURRENT.get()
    if record is not None:
        _add(record.errors, text)


@contextmanager
def recording() -> Iterator[AttemptRecord]:
    """Open a fresh record for the current request; restore the previous one after."""
    record = AttemptRecord()
    token = _CURRENT.set(record)
    try:
        yield record
    finally:
        _CURRENT.reset(token)


__all__ = ["AttemptRecord", "MAX_CHARS", "MAX_ENTRIES", "note_error", "note_sql", "recording"]
