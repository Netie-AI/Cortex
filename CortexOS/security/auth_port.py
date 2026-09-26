"""Request authorizer port: the engine side of the API auth seam (C2 boundary, TRUST-01).

``CortexOS`` may not import ``packs.*`` (``tests/contract/test_import_boundaries.py``).
The API-key RBAC (viewer / steward / admin) lives in
``packs/dms/security/api_auth.py``, so the dependency is inverted the same way
as :mod:`CortexOS.audit.ledger_registry` and :mod:`CortexOS.security.redact_port`:
the engine owns the port declared here, the pack pushes its implementation in
with :func:`register_authorizer`, and engine routes pull it back out through
the :func:`require_role` FastAPI dependency. The arrow only ever points
packs -> CortexOS.

Fail-closed rules (issue #257):

* No registered authorizer means the gated route refuses with 503. It never
  falls through to the handler.
* An authorizer that raises anything other than an ``HTTPException`` refuses
  with 503 too. Its 401 / 403 pass through unchanged.
* The engine re-checks the returned principal's role, so an authorizer that
  hands back a weaker role than was asked for still gets a 403.
* Refusal bodies are fixed strings. They never carry host paths or exception
  text.

The route handlers do not change: a caller the authorizer accepts sees exactly
the response the route gave before the gate existed.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Final, Protocol, runtime_checkable

from fastapi import HTTPException, Request

ROLES: Final[tuple[str, ...]] = ("viewer", "steward", "admin")
_ROLE_RANK: Final[dict[str, int]] = {role: rank for rank, role in enumerate(ROLES)}

NO_AUTHORIZER_DETAIL: Final[str] = "API authorization is not configured on this engine"
AUTHORIZER_FAILED_DETAIL: Final[str] = "API authorization is unavailable"


@runtime_checkable
class Principal(Protocol):
    """Who the authorizer says is calling. ``packs.dms`` ``Caller`` satisfies it."""

    @property
    def role(self) -> str: ...

    @property
    def actor(self) -> str: ...


@runtime_checkable
class RequestAuthorizer(Protocol):
    """Decide whether ``request`` may act with at least ``min_role``.

    Return the principal on success. Raise ``HTTPException(401)`` when no valid
    credential was presented and ``HTTPException(403)`` when the role is too low.
    """

    async def authorize(self, request: Request, min_role: str) -> Principal: ...


class AuthorizerNotRegistered(RuntimeError):
    """No vertical pack registered a request authorizer for this install."""


_authorizer: RequestAuthorizer | None = None


def register_authorizer(authorizer: RequestAuthorizer) -> None:
    """Install the active authorizer. Called by the owning pack, never by the engine."""
    global _authorizer
    _authorizer = authorizer


def clear_authorizer() -> None:
    """Drop the registered authorizer (pack swap / test teardown)."""
    global _authorizer
    _authorizer = None


def registered_authorizer() -> RequestAuthorizer | None:
    """The currently registered authorizer, without triggering a pack import."""
    return _authorizer


def resolve_authorizer() -> RequestAuthorizer:
    """Return the registered authorizer, importing the active pack once so it can register."""
    if _authorizer is None:
        _load_active_pack()
    if _authorizer is None:
        raise AuthorizerNotRegistered(
            "No request authorizer is registered. The active vertical pack must call "
            "CortexOS.security.auth_port.register_authorizer()."
        )
    return _authorizer


def _load_active_pack() -> None:
    """Import the configured pack so any module-level registration runs.

    Resolved dynamically on purpose: a static ``import packs.`` here would put
    the engine on the wrong side of the C2 boundary.
    """
    try:
        from CortexOS.config import get_config

        importlib.import_module(f"packs.{get_config().pack}")
    except Exception:  # noqa: BLE001 - a missing pack means no authorizer, which refuses
        return


def startup_warnings() -> list[str]:
    """What the app should log loudly at startup about request authorization.

    Resolves the authorizer (importing the active pack once) and asks it for its
    own warnings through an optional ``startup_warnings()`` method, so the pack
    can report an explicit auth bypass without the engine knowing its env names.
    An authorizer without that method contributes nothing; it is not part of the
    :class:`RequestAuthorizer` protocol. No authorizer at all is itself a warning,
    because every gated engine route will then refuse with 503.
    """
    try:
        authorizer = resolve_authorizer()
    except AuthorizerNotRegistered:
        return [
            "No request authorizer is registered: every gated engine route will "
            "refuse with 503 until the active pack registers one."
        ]
    hook = getattr(authorizer, "startup_warnings", None)
    if not callable(hook):
        return []
    try:
        return [str(w) for w in (hook() or [])]
    except Exception:  # noqa: BLE001 - a broken hook must not stop the app starting
        return ["The request authorizer could not report its startup state."]


def log_startup_warnings(logger: logging.Logger | None = None) -> list[str]:
    """Log each :func:`startup_warnings` entry at WARNING and return them."""
    log = logger or logging.getLogger("CortexOS.security.auth")
    warnings = startup_warnings()
    for text in warnings:
        log.warning("SECURITY WARNING: %s", text)
    return warnings


def role_at_least(have: str, need: str) -> bool:
    return _ROLE_RANK.get(have, -1) >= _ROLE_RANK.get(need, len(ROLES))


def require_role(min_role: str) -> Any:
    """FastAPI dependency: refuse unless the registered authorizer grants ``min_role``."""
    if min_role not in _ROLE_RANK:
        raise ValueError(f"unknown role {min_role!r}; expected one of {ROLES}")

    async def _dep(request: Request) -> Principal:
        try:
            authorizer = resolve_authorizer()
        except AuthorizerNotRegistered:
            raise HTTPException(status_code=503, detail=NO_AUTHORIZER_DETAIL) from None
        try:
            principal = await authorizer.authorize(request, min_role)
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 - fail closed, and keep the error text out of the body
            raise HTTPException(status_code=503, detail=AUTHORIZER_FAILED_DETAIL) from None
        role = str(getattr(principal, "role", "") or "")
        if not role_at_least(role, min_role):
            raise HTTPException(status_code=403, detail=f"Requires role {min_role!r} or higher")
        return principal

    return _dep


# #265 (P24.4): a route that can spend a model credential names this reason when
# it refuses an unauthenticated or under-privileged caller, so every spend path
# refuses the same way. Loopback callers are not exempt.
SPEND_REQUIRES_AUTH: Final[str] = "spend_requires_auth"
SPEND_REQUIRES_AUTH_MESSAGE: Final[str] = (
    "A call that can spend a model credential needs an authenticated caller "
    "(X-API-Key or Bearer); local callers are not exempt"
)


def require_spend_auth(min_role: str = "viewer") -> Any:
    """FastAPI dependency: :func:`require_role` for routes that can spend a model credential.

    Same decision and status codes as :func:`require_role` (401 no credential,
    403 role too low, 503 no or broken authorizer). A 401 / 403 body carries the
    named reason :data:`SPEND_REQUIRES_AUTH`; a 503 keeps its fixed body. It
    runs before the handler, so a refused caller never reaches a provider or
    model call.
    """
    gate = require_role(min_role)

    async def _dep(request: Request) -> Principal:
        try:
            principal: Principal = await gate(request)
        except HTTPException as exc:
            if exc.status_code not in (401, 403):
                # 503 is the engine's own state (no or broken authorizer), not
                # the caller's; its fixed body passes through unchanged.
                raise
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": SPEND_REQUIRES_AUTH,
                    "message": SPEND_REQUIRES_AUTH_MESSAGE,
                    "auth": exc.detail,
                },
            ) from None
        return principal

    return _dep


__all__ = [
    "AUTHORIZER_FAILED_DETAIL",
    "AuthorizerNotRegistered",
    "NO_AUTHORIZER_DETAIL",
    "Principal",
    "ROLES",
    "RequestAuthorizer",
    "SPEND_REQUIRES_AUTH",
    "SPEND_REQUIRES_AUTH_MESSAGE",
    "clear_authorizer",
    "log_startup_warnings",
    "register_authorizer",
    "registered_authorizer",
    "require_role",
    "require_spend_auth",
    "resolve_authorizer",
    "role_at_least",
    "startup_warnings",
]
