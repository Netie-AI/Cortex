"""The DMS implementation of the engine's request authorizer port (TRUST-01, #257).

Same rules as :func:`api_auth.get_caller` and :func:`api_auth.require_role`, so
an engine route gated through ``CortexOS.security.auth_port`` accepts exactly
the keys and roles a DMS route accepts, including ``DMS_AUTH_DISABLED``.
Registered from :func:`packs.dms.register_engine_seams`; the arrow points
packs -> CortexOS.
"""

from __future__ import annotations

from fastapi import Request

from packs.dms.security import api_auth


class DmsRequestAuthorizer:
    async def authorize(self, request: Request, min_role: str) -> api_auth.Caller:
        caller = await api_auth.get_caller(
            x_api_key=request.headers.get("X-API-Key"),
            authorization=request.headers.get("Authorization"),
        )
        return await api_auth.require_role(min_role)(caller)  # type: ignore[arg-type]

    def startup_warnings(self) -> list[str]:
        out: list[str] = []
        if not api_auth.auth_required():
            out.append(
                "DMS_AUTH_DISABLED is set: every gated route treats every caller, "
                "including anonymous ones, as admin. Local development only."
            )
        elif api_auth._keys_source() == api_auth._DEMO_KEYS:
            out.append(
                "DMS_API_KEYS is unset, so the published dms-demo-* keys authenticate. "
                "Set DMS_API_KEYS (or DMS_REFUSE_DEMO_KEYS=1) outside local development."
            )
        return out


def register_request_authorizer() -> None:
    from CortexOS.security.auth_port import register_authorizer

    register_authorizer(DmsRequestAuthorizer())
