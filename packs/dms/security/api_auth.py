"""F7 remainder — API-key RBAC (viewer / steward / admin)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Literal

from fastapi import Depends, Header, HTTPException

Role = Literal["viewer", "steward", "admin"]

ROLES: Final[tuple[str, ...]] = ("viewer", "steward", "admin")
_ROLE_RANK: Final[dict[str, int]] = {"viewer": 0, "steward": 1, "admin": 2}

_DEMO_KEYS: Final[str] = (
    "viewer:dms-demo-viewer-key;"
    "steward:dms-demo-steward-key;"
    "admin:dms-demo-admin-key"
)


@dataclass(frozen=True, slots=True)
class Caller:
    role: Role
    actor: str


def _keys_source() -> str:
    return (os.environ.get("DMS_API_KEYS") or "").strip() or _DEMO_KEYS


def auth_required() -> bool:
    if os.environ.get("DMS_AUTH_DISABLED", "").lower() in ("1", "true", "yes"):
        return False
    return True


def parse_api_keys(source: str | None = None) -> dict[str, Caller]:
    """Parse ``role:secret`` pairs separated by ``;`` into key → Caller map."""
    raw = source if source is not None else _keys_source()
    mapping: dict[str, Caller] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        role, key = part.split(":", 1)
        role = role.strip().lower()
        key = key.strip()
        if role not in _ROLE_RANK or not key:
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


def resolve_caller(api_key: str | None) -> Caller | None:
    if not api_key:
        return None
    return parse_api_keys().get(api_key)


def role_at_least(have: str, need: str) -> bool:
    return _ROLE_RANK.get(have, -1) >= _ROLE_RANK.get(need, 99)


async def get_caller(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> Caller:
    if not auth_required():
        return Caller(role="admin", actor="auth_disabled")

    key = extract_api_key(x_api_key, authorization)
    caller = resolve_caller(key)
    if caller is None:
        raise HTTPException(status_code=401, detail="Valid API key required (X-API-Key or Bearer)")
    return caller


def require_role(min_role: Role):
    async def _dep(caller: Caller = Depends(get_caller)) -> Caller:
        if not role_at_least(caller.role, min_role):
            raise HTTPException(
                status_code=403,
                detail=f"Requires role {min_role!r} or higher (caller={caller.role!r})",
            )
        return caller

    return _dep
