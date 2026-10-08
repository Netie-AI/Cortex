"""The DMS implementation of the engine's request authorizer port (TRUST-01, #257).

Same keys and roles as :func:`api_auth.get_caller` / :func:`api_auth.require_role`,
including the ``DMS_AUTH_DISABLED`` local-dev opt-in, with one rule on top
(T2-FAILCLOSED, #263): a key that was ever published (the ``dms-demo-*``
fallback) or is a template placeholder never authenticates an engine-gated
route, even when ``DMS_API_KEYS`` lists it. With ``DMS_API_KEYS`` unset, only an
OpenVault-verified ``ov_`` token gets through.

Routes that depend on ``api_auth.require_role`` directly (the DMS contract
caller's ``/dms/*`` paths) keep the demo fallback until ``api_auth`` drops it.

Registered from :func:`packs.dms.register_engine_seams`; the arrow points
packs -> CortexOS.
"""

from __future__ import annotations

import os
from typing import Final

from fastapi import HTTPException, Request

from packs.dms.security import api_auth

PUBLISHED_KEYS: Final[frozenset[str]] = frozenset(
    {"dms-demo-viewer-key", "dms-demo-steward-key", "dms-demo-admin-key"}
)
PLACEHOLDER_PREFIX: Final[str] = "replace_with"

# Identical to api_auth's refusal, so the body never says which rule refused.
_REFUSED_DETAIL: Final[str] = "Valid API key required (X-API-Key or Bearer)"


def is_unusable_key(key: str) -> bool:
    return key in PUBLISHED_KEYS or key.lower().startswith(PLACEHOLDER_PREFIX)


def _configured_usable_keys() -> list[str]:
    configured = api_auth.parse_api_keys(os.environ.get("DMS_API_KEYS") or "")
    return [key for key in configured if not is_unusable_key(key)]


class DmsRequestAuthorizer:
    async def authorize(self, request: Request, min_role: str) -> api_auth.Caller:
        x_api_key = request.headers.get("X-API-Key")
        authorization = request.headers.get("Authorization")
        if api_auth.auth_required():
            key = api_auth.extract_api_key(x_api_key, authorization)
            if key is not None and is_unusable_key(key):
                raise HTTPException(status_code=401, detail=_REFUSED_DETAIL)
        caller = await api_auth.get_caller(x_api_key=x_api_key, authorization=authorization)
        return await api_auth.require_role(min_role)(caller)  # type: ignore[arg-type]

    def startup_warnings(self) -> list[str]:
        if not api_auth.auth_required():
            return [
                "DMS_AUTH_DISABLED is set: every gated route treats every caller, "
                "including anonymous ones, as admin. Local development only."
            ]
        out: list[str] = []
        if not _configured_usable_keys():
            out.append(
                "DMS_API_KEYS has no usable key: engine-gated routes accept only "
                "OpenVault-verified ov_ tokens."
            )
        if api_auth.resolve_caller("dms-demo-viewer-key") is not None:
            out.append(
                "The published dms-demo-* keys still authenticate on routes gated by "
                "packs/dms/security/api_auth.py. Set DMS_API_KEYS or DMS_REFUSE_DEMO_KEYS=1."
            )
        return out


def register_request_authorizer() -> None:
    from CortexOS.security.auth_port import register_authorizer

    register_authorizer(DmsRequestAuthorizer())
