"""Session → VerifiedManifest registry for contract ask (C4-min).

DMS binds via ``POST /v1/contract/submit`` (plan.kind=session_bind or sql).
``ask`` resolves by ``session_id`` and fails closed when unbound or expired.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from CortexOS.execution.manifest import (
    ManifestExpired,
    ManifestMalformed,
    VerifiedManifest,
)


class SessionUnbound(Exception):
    code = "session_unbound"


class SpaceUnbound(SessionUnbound):
    """No signed grant is bound for this (session, Space). Never fall back."""

    code = "space_unbound"


class SessionExpired(Exception):
    code = "session_expired"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_expires(raw: str) -> datetime:
    text = (raw or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ManifestMalformed(f"expires_at is not a usable timestamp: {raw!r}") from exc
    if when.tzinfo is None:
        raise ManifestMalformed(f"expires_at has no timezone offset: {raw!r}")
    return when


@dataclass(slots=True)
class SessionBinding:
    verified: VerifiedManifest
    expires_at: datetime
    bound_at: datetime


def _named_space(verified: VerifiedManifest) -> str | None:
    text = (verified.manifest.space_id or "").strip()
    return text or None


class SessionManifestRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_session: dict[str, SessionBinding] = {}
        self._by_space: dict[tuple[str, str], SessionBinding] = {}

    def bind(self, verified: VerifiedManifest, *, now: datetime | None = None) -> SessionBinding:
        when = now or _now()
        expires_at = _parse_expires(verified.manifest.expires_at)
        if expires_at <= when:
            raise ManifestExpired(f"expired at {verified.manifest.expires_at}")
        session_id = verified.manifest.session_id
        if not session_id:
            raise ManifestMalformed("session_id is missing")
        binding = SessionBinding(verified=verified, expires_at=expires_at, bound_at=when)
        with self._lock:
            self._by_session[session_id] = binding
            space = _named_space(verified)
            if space:
                self._by_space[(session_id, space)] = binding
        return binding

    def resolve(
        self,
        session_id: str | None,
        *,
        space_id: str | None = None,
        now: datetime | None = None,
    ) -> VerifiedManifest:
        if not session_id or not str(session_id).strip():
            raise SessionUnbound("session_id is required for live ask")
        session_id = str(session_id).strip()
        named = (space_id or "").strip() or None
        when = now or _now()
        with self._lock:
            if named:
                key = (session_id, named)
                binding = self._by_space.get(key)
                if binding is None:
                    raise SpaceUnbound(
                        f"no verified manifest bound for session {session_id!r} Space {named!r}"
                    )
                if binding.expires_at <= when:
                    self._by_space.pop(key, None)
                    if self._by_session.get(session_id) is binding:
                        self._by_session.pop(session_id, None)
                    raise SessionExpired(
                        f"session binding expired at {binding.expires_at.isoformat()}"
                    )
                return binding.verified
            binding = self._by_session.get(session_id)
            if binding is None:
                raise SessionUnbound(f"no verified manifest bound for session {session_id!r}")
            if binding.expires_at <= when:
                self._by_session.pop(session_id, None)
                space = _named_space(binding.verified)
                if space:
                    self._by_space.pop((session_id, space), None)
                raise SessionExpired(f"session binding expired at {binding.expires_at.isoformat()}")
            return binding.verified

    def clear(self, session_id: str | None = None) -> None:
        with self._lock:
            if session_id is None:
                self._by_session.clear()
                self._by_space.clear()
                return
            self._by_session.pop(session_id, None)
            for key in [k for k in self._by_space if k[0] == session_id]:
                self._by_space.pop(key, None)


_REGISTRY = SessionManifestRegistry()


def get_session_registry() -> SessionManifestRegistry:
    return _REGISTRY


def reset_session_registry_for_tests() -> SessionManifestRegistry:
    _REGISTRY.clear()
    return _REGISTRY


__all__ = [
    "SessionBinding",
    "SessionExpired",
    "SessionManifestRegistry",
    "SessionUnbound",
    "SpaceUnbound",
    "get_session_registry",
    "reset_session_registry_for_tests",
]
