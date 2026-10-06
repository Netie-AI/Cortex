"""F7 remainder — API-key RBAC (viewer / steward / admin).

``ov_`` keys are checked against OpenVault ``POST /api/apikeys/verify`` with
Cortex's own OpenVault service bearer, read from ``CORTEX_OV_SERVICE_TOKEN_FILE``.
That bearer is never the OpenVault admin token, and neither it nor the key
being verified is ever logged, put in an error, or returned.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from fastapi import Depends, Header, HTTPException

log = logging.getLogger(__name__)

Role = Literal["viewer", "steward", "admin"]

ROLES: Final[tuple[str, ...]] = ("viewer", "steward", "admin")
_ROLE_RANK: Final[dict[str, int]] = {"viewer": 0, "steward": 1, "admin": 2}

_DEMO_KEYS: Final[str] = (
    "viewer:dms-demo-viewer-key;"
    "steward:dms-demo-steward-key;"
    "admin:dms-demo-admin-key"
)

OV_SERVICE_TOKEN_FILE_ENV: Final[str] = "CORTEX_OV_SERVICE_TOKEN_FILE"
OV_VERIFY_DEFAULT_URL: Final[str] = "http://127.0.0.1:8080"
OV_VERIFY_PATH: Final[str] = "/api/apikeys/verify"
_OV_ADMIN_TOKEN_PATH_ENV: Final[str] = "OPENVAULT_ADMIN_TOKEN_PATH"
_OV_ADMIN_TOKEN_FILENAME: Final[str] = "admin_token"
_OV_VERIFY_TIMEOUT_S = 3.0
_OV_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,128}")

DENY_MISSING_KEY: Final[str] = "missing_key"
DENY_INVALID_KEY: Final[str] = "invalid_key"
DENY_OV_BASE_URL_CONFLICT: Final[str] = "ov_base_url_conflict"
DENY_OV_SERVICE_TOKEN_MISSING: Final[str] = "ov_service_token_missing"
DENY_OV_SERVICE_TOKEN_INVALID: Final[str] = "ov_service_token_invalid"
DENY_OV_SERVICE_TOKEN_IS_ADMIN: Final[str] = "ov_service_token_is_admin"
DENY_OV_VERIFY_UNAUTHORIZED: Final[str] = "ov_verify_unauthorized"
DENY_OV_VERIFY_FORBIDDEN: Final[str] = "ov_verify_forbidden"
DENY_OV_VERIFY_UNREACHABLE: Final[str] = "ov_verify_unreachable"
DENY_OV_VERIFY_UNEXPECTED_STATUS: Final[str] = "ov_verify_unexpected_status"
DENY_OV_VERIFY_MALFORMED: Final[str] = "ov_verify_malformed"


@dataclass(frozen=True, slots=True)
class Caller:
    role: Role
    actor: str
    key_id: str = ""
    tier: str = ""


@dataclass(frozen=True, slots=True)
class Deny:
    """A refused credential. ``reason`` is one of the ``DENY_*`` names, never a secret."""

    reason: str


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


def _ov_deny(reason: str, status: int | None = None) -> Deny:
    log.warning("openvault key verify denied: reason=%s status=%s", reason, status)
    return Deny(reason)


def ov_verify_base_url() -> str | Deny:
    """The one OpenVault this Cortex uses; the :8080 vault when none is configured."""
    from CortexOS.integrations import openvault_client

    if openvault_client.openvault_base_url_conflict():
        return Deny(DENY_OV_BASE_URL_CONFLICT)
    if any((os.environ.get(name) or "").strip() for name in openvault_client._BASE_URL_ENVS):
        return openvault_client.openvault_base_url()
    return OV_VERIFY_DEFAULT_URL


def _is_ov_admin_token_file(path: Path) -> bool:
    if path.name == _OV_ADMIN_TOKEN_FILENAME:
        return True
    admin = (os.environ.get(_OV_ADMIN_TOKEN_PATH_ENV) or "").strip()
    if not admin:
        return False
    admin_path = Path(admin).expanduser()
    try:
        return os.path.samefile(path, admin_path)
    except OSError:
        return path.resolve() == admin_path.resolve()


def ov_service_token() -> str | Deny:
    """Cortex's own OpenVault service bearer from its secret file. Never the admin token."""
    raw = (os.environ.get(OV_SERVICE_TOKEN_FILE_ENV) or "").strip()
    if not raw:
        return Deny(DENY_OV_SERVICE_TOKEN_MISSING)
    path = Path(raw).expanduser()
    if _is_ov_admin_token_file(path):
        return Deny(DENY_OV_SERVICE_TOKEN_IS_ADMIN)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return Deny(DENY_OV_SERVICE_TOKEN_MISSING)
    if not token:
        return Deny(DENY_OV_SERVICE_TOKEN_MISSING)
    if not token.isprintable() or any(ch.isspace() for ch in token):
        return Deny(DENY_OV_SERVICE_TOKEN_INVALID)
    return token


def _caller_from_verify_body(body: Any) -> Caller | Deny:
    """Strict ``{ok, valid, key_id, tier}``. Anything else is a deny."""
    if not isinstance(body, dict) or body.get("ok") is not True:
        return _ov_deny(DENY_OV_VERIFY_MALFORMED, 200)
    valid = body.get("valid")
    if valid is False:
        return Deny(DENY_INVALID_KEY)
    if valid is not True:
        return _ov_deny(DENY_OV_VERIFY_MALFORMED, 200)
    key_id = body.get("key_id")
    tier = body.get("tier")
    if not isinstance(key_id, str) or _OV_ID.fullmatch(key_id) is None:
        return _ov_deny(DENY_OV_VERIFY_MALFORMED, 200)
    if not isinstance(tier, str) or _OV_ID.fullmatch(tier) is None:
        return _ov_deny(DENY_OV_VERIFY_MALFORMED, 200)
    return Caller(role="viewer", actor=f"ov_{key_id}", key_id=key_id, tier=tier)


def verify_openvault_key(token: str) -> Caller | Deny:
    """Ask OpenVault whether an ``ov_`` key is live. Fails closed with a named reason."""
    from CortexOS.integrations import openvault_client

    base = ov_verify_base_url()
    if isinstance(base, Deny):
        return _ov_deny(base.reason)
    service = ov_service_token()
    if isinstance(service, Deny):
        return _ov_deny(service.reason)
    try:
        status, body = openvault_client.request_json(
            "POST",
            OV_VERIFY_PATH,
            body={"token": token},
            headers={"Authorization": f"Bearer {service}"},
            timeout=_OV_VERIFY_TIMEOUT_S,
            base=base,
        )
    except Exception:
        return _ov_deny(DENY_OV_VERIFY_UNREACHABLE)
    if status == 0:
        return _ov_deny(DENY_OV_VERIFY_UNREACHABLE, status)
    if status == 401:
        return _ov_deny(DENY_OV_VERIFY_UNAUTHORIZED, status)
    if status == 403:
        return _ov_deny(DENY_OV_VERIFY_FORBIDDEN, status)
    if status != 200:
        return _ov_deny(DENY_OV_VERIFY_UNEXPECTED_STATUS, status)
    return _caller_from_verify_body(body)


def resolve_auth(api_key: str | None) -> Caller | Deny:
    if not api_key:
        return Deny(DENY_MISSING_KEY)
    found = parse_api_keys().get(api_key)
    if found is not None:
        return found
    if api_key.startswith("ov_"):
        return verify_openvault_key(api_key)
    return Deny(DENY_INVALID_KEY)


def resolve_caller(api_key: str | None) -> Caller | None:
    """``resolve_auth`` for callers that only need allow/deny. OpenVault denies are logged."""
    decision = resolve_auth(api_key)
    return decision if isinstance(decision, Caller) else None


def role_at_least(have: str, need: str) -> bool:
    return _ROLE_RANK.get(have, -1) >= _ROLE_RANK.get(need, 99)


async def get_caller(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> Caller:
    if not auth_required():
        return Caller(role="admin", actor="auth_disabled")

    key = extract_api_key(x_api_key, authorization)
    decision = resolve_auth(key)
    if isinstance(decision, Deny):
        raise HTTPException(
            status_code=401,
            detail=f"Valid API key required (X-API-Key or Bearer): {decision.reason}",
        )
    return decision


def require_role(min_role: Role):
    async def _dep(caller: Caller = Depends(get_caller)) -> Caller:
        if not role_at_least(caller.role, min_role):
            raise HTTPException(
                status_code=403,
                detail=f"Requires role {min_role!r} or higher (caller={caller.role!r})",
            )
        try:
            from packs.dms.audit.ledger import set_rls_context

            set_rls_context(role=caller.role, tenant_id="default")
        except Exception:
            pass
        return caller

    return _dep
