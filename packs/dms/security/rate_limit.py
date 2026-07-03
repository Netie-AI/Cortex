"""F7 remainder — token-bucket rate limiting for /dms/* routes."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = max(1, per_minute)
        self._buckets: dict[str, _Bucket] = defaultdict(lambda: _Bucket(tokens=float(per_minute), updated=time.monotonic()))
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        refill_rate = self.per_minute / 60.0
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets[key]
            elapsed = now - bucket.updated
            bucket.tokens = min(float(self.per_minute), bucket.tokens + elapsed * refill_rate)
            bucket.updated = now
            if bucket.tokens < 1.0:
                return False
            bucket.tokens -= 1.0
            return True


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        per_min = int(os.environ.get("DMS_RATE_LIMIT_PER_MIN", "120"))
        _limiter = RateLimiter(per_min)
    return _limiter


def reset_limiter(per_minute: int | None = None) -> None:
    global _limiter
    per_min = per_minute if per_minute is not None else int(os.environ.get("DMS_RATE_LIMIT_PER_MIN", "120"))
    _limiter = RateLimiter(per_min)


class DMSRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if not path.startswith("/dms/"):
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        if not get_limiter().allow(client):
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded — retry shortly"},
            )
        return await call_next(request)
