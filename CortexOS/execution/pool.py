"""Single read-pool for C4-min personal / demo installs.

Full multi-pool memory broker is T10 / full C4. This module exposes one
configured pool with a semaphore, queue timeout, and statement-timeout hint.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


class PoolSaturated(Exception):
    """No slot within ``queue_timeout_s``. Mapped to HTTP 429 / ``pool_saturated``."""

    code = "pool_saturated"


@dataclass(frozen=True, slots=True)
class PoolConfig:
    pool_id: str
    max_concurrency: int
    queue_timeout_s: float
    statement_timeout_s: float


def load_pool_config() -> PoolConfig:
    return PoolConfig(
        pool_id=os.environ.get("CORTEX_READ_POOL_ID", "default").strip() or "default",
        max_concurrency=max(1, int(os.environ.get("CORTEX_READ_POOL_CONCURRENCY", "4"))),
        queue_timeout_s=float(os.environ.get("CORTEX_READ_POOL_QUEUE_TIMEOUT_S", "5")),
        statement_timeout_s=float(os.environ.get("CORTEX_STATEMENT_TIMEOUT_S", "30")),
    )


class ReadPool:
    """Process-local semaphore around warehouse reads."""

    def __init__(self, config: PoolConfig | None = None) -> None:
        self.config = config or load_pool_config()
        self._sem = threading.Semaphore(self.config.max_concurrency)
        self._lock = threading.Lock()
        self._active = 0

    @property
    def pool_id(self) -> str:
        return self.config.pool_id

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    @contextmanager
    def acquire(self, *, pool_id: str | None = None) -> Iterator[float]:
        """Acquire a slot. Yields queue wait seconds. Raises ``PoolSaturated``.

        C4-min has one physical semaphore. ``pool_id`` is recorded by the caller
        for telemetry; request↔manifest equality is enforced in ``submit``, not
        here. Optional ``CORTEX_READ_POOL_ID`` documents the preferred id for DMS.
        """
        _ = pool_id  # reserved for multi-pool routing (T10 / full C4)
        started = time.perf_counter()
        if not self._sem.acquire(timeout=self.config.queue_timeout_s):
            raise PoolSaturated(
                f"pool {self.config.pool_id!r} saturated "
                f"(concurrency={self.config.max_concurrency}, "
                f"queue_timeout_s={self.config.queue_timeout_s})"
            )
        queue_ms = (time.perf_counter() - started) * 1000.0
        with self._lock:
            self._active += 1
        try:
            yield queue_ms
        finally:
            with self._lock:
                self._active -= 1
            self._sem.release()


_DEFAULT_POOL: ReadPool | None = None
_POOL_LOCK = threading.Lock()


def get_read_pool() -> ReadPool:
    global _DEFAULT_POOL
    with _POOL_LOCK:
        if _DEFAULT_POOL is None:
            _DEFAULT_POOL = ReadPool()
        return _DEFAULT_POOL


def reset_read_pool_for_tests(config: PoolConfig | None = None) -> ReadPool:
    """Replace the process pool (tests only)."""
    global _DEFAULT_POOL
    with _POOL_LOCK:
        _DEFAULT_POOL = ReadPool(config)
        return _DEFAULT_POOL


__all__ = [
    "PoolConfig",
    "PoolSaturated",
    "ReadPool",
    "get_read_pool",
    "load_pool_config",
    "reset_read_pool_for_tests",
]
