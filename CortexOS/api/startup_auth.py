"""Fail closed when auth is switched off outside explicit dev mode.

``DMS_AUTH_DISABLED`` truthy values are ``1``, ``true``, and ``yes`` (same set as
``packs.dms.security.api_auth.auth_required``). That switch makes ``get_caller``
return admin. It is allowed only when ``CORTEX_DEV_MODE`` is truthy with the
same set.

There is no other dev-mode switch. ``PACK`` selects a vertical. ``CORTEX_PROFILE``
selects ``core`` or ``full``. Neither one is dev mode.

Images must not set ``DMS_AUTH_DISABLED``. Local auth-off is a gitignored
``dev.env`` copied from ``dev.env.example`` and loaded by
``docker-compose.dev.yml``.
"""

from __future__ import annotations

import logging
import os

_LOG = logging.getLogger("cortex.startup")

_TRUTHY = frozenset({"1", "true", "yes"})

DEV_MODE_ENV = "CORTEX_DEV_MODE"
AUTH_DISABLED_ENV = "DMS_AUTH_DISABLED"
AUTH_DISABLED_WITHOUT_DEV_MODE = "AUTH_DISABLED_WITHOUT_DEV_MODE"


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in _TRUTHY


class AuthDisabledWithoutDevMode(RuntimeError):
    """Process must not start as admin without an explicit dev-mode opt-in."""

    code = AUTH_DISABLED_WITHOUT_DEV_MODE

    def __init__(self) -> None:
        super().__init__(self.code)


def refuse_auth_disabled_without_dev_mode() -> None:
    """Raise before the ASGI app exists when auth-off has no dev-mode opt-in."""
    if not _truthy(AUTH_DISABLED_ENV):
        return
    if _truthy(DEV_MODE_ENV):
        return
    _LOG.error(
        "%s: %s is set without %s; refusing to start",
        AUTH_DISABLED_WITHOUT_DEV_MODE,
        AUTH_DISABLED_ENV,
        DEV_MODE_ENV,
    )
    raise AuthDisabledWithoutDevMode()
