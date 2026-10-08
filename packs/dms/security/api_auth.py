"""F7 remainder — API-key RBAC (viewer / steward / admin)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Literal

from fastapi import Depends, Header, HTTPException, Request

Role = Literal["viewer", "steward", "admin"]

ROLES: Final[tuple[str, ...]] = ("viewer", "steward", "admin")
_ROLE_RANK: Final[dict[str, int]] = {"viewer": 0, "steward": 1, "admin": 2}

# Constructor session cookie. Same key store as X-API-Key / Bearer.
SESSION_COOKIE = "cortex_api_key"

_DEMO_KEYS: Final[str] = (
    "viewer:dms-demo-viewer-key;"
    "steward:dms-demo-steward-key;"
    "admin:dms-demo-admin-key"
)


@dataclass(frozen=True, slots=True)
class Caller:
    role: Role
    actor: str


def _refuse_demo_keys() -> bool:
    return os.environ.get("DMS_REFUSE_DEMO_KEYS", "").lower() in ("1", "true", "yes")


def _keys_source() -> str:
    raw = (os.environ.get("DMS_API_KEYS") or "").strip()
    if raw:
        return raw
    if _refuse_demo_keys():
        return ""
    return _DEMO_KEYS


def auth_required() -> bool:
    if os.environ.get("DMS_AUTH_DISABLED", "").lower() in ("1", "true", "yes"):
        return False
    return True


def parse_api_keys(source: str | None = None) -> dict[str, Caller]:
    """Parse ``role:secret`` pairs separated by ``;`` into key → Caller map."""
    raw = source if source is not None else _keys_source()
    mapping: dict[str, Caller] = {}
    ambiguous: set[str] = set()
    for part in raw.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        role, key = part.split(":", 1)
        role = role.strip().lower()
        key = key.strip()
        if role not in _ROLE_RANK or not key or key in ambiguous:
            continue
        existing = mapping.get(key)
        if existing is not None and existing.role != role:
            # One secret, two roles: do not guess. The key cannot authenticate.
            ambiguous.add(key)
            del mapping[key]
            continue
        mapping[key] = Caller(role=role, actor=f"api_{role}")  # type: ignore[arg-type]
    return mapping


def extract_api_key(
    x_api_key: str | None,
    authorization: str | None,
) -> str | None:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


def _caller_from_openvault(token: str) -> Caller | None:
    from CortexOS.integrations.openvault_client import post_json

    out = post_json("/api/apikeys/verify", {"token": token}, timeout=3.0)
    if not out or out.get("ok") is not True:
        return None
    if out.get("valid") is False:
        return None
    key = out.get("key") if isinstance(out.get("key"), dict) else {}
    actor = str(key.get("key_id") or key.get("id") or out.get("key_id") or "")
    if not actor:
        return None
    return Caller(role="viewer", actor=f"ov_{actor}")


def resolve_caller(api_key: str | None) -> Caller | None:
    if not api_key:
        return None
    found = parse_api_keys().get(api_key)
    if found is not None:
        return found
    if api_key.startswith("ov_"):
        return _caller_from_openvault(api_key)
    return None


def role_at_least(have: str, need: str) -> bool:
    return _ROLE_RANK.get(have, -1) >= _ROLE_RANK.get(need, 99)


async def get_caller(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> Caller:
    if not auth_required():
        return Caller(role="admin", actor="auth_disabled")

    key = extract_api_key(x_api_key, authorization) or request.cookies.get(SESSION_COOKIE)
    caller = resolve_caller(key)
    if caller is None:
        raise HTTPException(status_code=401, detail="Valid API key required (X-API-Key or Bearer)")
    return caller


def _refuse_unless(caller: Caller, min_role: str) -> Caller:
    if not role_at_least(caller.role, min_role):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Requires role {min_role!r} or higher "
                f"(caller={caller.role!r} actor={caller.actor!r})"
            ),
        )
    try:
        from packs.dms.audit.ledger import set_rls_context

        set_rls_context(role=caller.role, tenant_id="default")
    except Exception:
        pass
    return caller


async def get_presented_caller(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> Caller:
    """Same key store as ``get_caller``, without the ``DMS_AUTH_DISABLED`` short-circuit.

    Routes that did not call ``get_caller`` on parent must not start honoring
    that flag. Honoring it would open them as admin.
    """
    key = extract_api_key(x_api_key, authorization) or request.cookies.get(SESSION_COOKIE)
    caller = resolve_caller(key)
    if caller is None:
        raise HTTPException(status_code=401, detail="Valid API key required (X-API-Key or Bearer)")
    return caller


def require_role(min_role: Role, *, honor_auth_disabled: bool = True):
    if honor_auth_disabled:
        async def _dep(caller: Caller = Depends(get_caller)) -> Caller:
            return _refuse_unless(caller, min_role)
    else:
        async def _dep(caller: Caller = Depends(get_presented_caller)) -> Caller:
            return _refuse_unless(caller, min_role)

    return _dep


# Model spend. Viewer can look. Steward and admin can spend. Any other role
# string fails closed inside role_at_least.
# ``require_spend`` still follows ``get_caller``, so ``DMS_AUTH_DISABLED``
# resolves to admin. That matches routes which already used ``get_caller``
# on parent (``POST /api/engine/run``).
# ``require_spend_key`` is the same rank and the same key store, and it does
# not honor the flag. Use it on routes that did not call ``get_caller`` before.
require_spend = require_role("steward")
require_spend_key = require_role("steward", honor_auth_disabled=False)
