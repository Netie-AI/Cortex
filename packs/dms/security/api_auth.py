"""F7 remainder — API-key RBAC (viewer / steward / admin).

``ov_`` keys are checked against OpenVault ``POST /api/apikeys/verify`` on the
loopback-bound verify listener (``CORTEX_OV_VERIFY_URL``, default :8080) with
Cortex's own OpenVault service bearer, read from the mode-0600 file named by
``CORTEX_OV_SERVICE_TOKEN_FILE``. OpenVault registered that bearer under
service_id ``cortex`` and admits it only when ``cortex`` is listed in
``OPENVAULT_VERIFY_SERVICES``; the id travels as the bearer, not as a field.
That bearer is never the OpenVault admin token, and neither it nor the key
being verified is ever logged, put in an error, or returned.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from fastapi import Depends, Header, HTTPException

log = logging.getLogger(__name__)

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

OV_SERVICE_ID: Final[str] = "cortex"
OV_SERVICE_TOKEN_FILE_ENV: Final[str] = "CORTEX_OV_SERVICE_TOKEN_FILE"
OV_VERIFY_URL_ENV: Final[str] = "CORTEX_OV_VERIFY_URL"
OV_VERIFY_DEFAULT_URL: Final[str] = "http://127.0.0.1:8080"
OV_VERIFY_PATH: Final[str] = "/api/apikeys/verify"
_OV_REFUSED_PORT: Final[int] = 5000
_OV_ADMIN_TOKEN_FILENAME: Final[str] = "admin_token"
_OV_SERVICE_TOKEN_MODE: Final[int] = 0o600
_OV_SERVICE_TOKEN_MAX_BYTES: Final[int] = 4096
_OV_VERIFY_TIMEOUT_S = 3.0
_OV_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,128}")

DENY_MISSING_KEY: Final[str] = "missing_key"
DENY_INVALID_KEY: Final[str] = "invalid_key"
DENY_OV_VERIFY_URL_INVALID: Final[str] = "ov_verify_url_invalid"
DENY_OV_VERIFY_URL_NOT_LOOPBACK: Final[str] = "ov_verify_url_not_loopback"
DENY_OV_VERIFY_URL_PORT_5000: Final[str] = "ov_verify_url_port_5000"
DENY_OV_SERVICE_TOKEN_MISSING: Final[str] = "ov_service_token_missing"
DENY_OV_SERVICE_TOKEN_UNREADABLE: Final[str] = "ov_service_token_unreadable"
DENY_OV_SERVICE_TOKEN_NOT_FILE: Final[str] = "ov_service_token_not_file"
DENY_OV_SERVICE_TOKEN_BAD_MODE: Final[str] = "ov_service_token_bad_mode"
DENY_OV_SERVICE_TOKEN_INVALID: Final[str] = "ov_service_token_invalid"
DENY_OV_SERVICE_TOKEN_IS_ADMIN: Final[str] = "ov_service_token_is_admin"
DENY_OV_VERIFY_UNAUTHORIZED: Final[str] = "ov_verify_unauthorized"
DENY_OV_VERIFY_FORBIDDEN: Final[str] = "ov_verify_forbidden"
DENY_OV_VERIFY_RATE_LIMITED: Final[str] = "ov_verify_rate_limited"
DENY_OV_VERIFY_SERVER_ERROR: Final[str] = "ov_verify_server_error"
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


def _ov_deny(reason: str, status: int | None = None) -> Deny:
    log.warning(
        "openvault key verify denied: reason=%s status=%s service_id=%s",
        reason,
        status,
        OV_SERVICE_ID,
    )
    return Deny(reason)


def ov_verify_base_url() -> str | Deny:
    """The loopback-bound OpenVault verify listener. Never the :5000 listener."""
    from CortexOS.integrations.openvault_client import is_loopback_url

    raw = (os.environ.get(OV_VERIFY_URL_ENV) or "").strip() or OV_VERIFY_DEFAULT_URL
    try:
        parts = urllib.parse.urlsplit(raw)
        port = parts.port
    except ValueError:
        return Deny(DENY_OV_VERIFY_URL_INVALID)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or port is None
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        return Deny(DENY_OV_VERIFY_URL_INVALID)
    if port == _OV_REFUSED_PORT:
        return Deny(DENY_OV_VERIFY_URL_PORT_5000)
    if not is_loopback_url(raw):
        return Deny(DENY_OV_VERIFY_URL_NOT_LOOPBACK)
    return f"{parts.scheme}://{parts.netloc}"


def _read_service_token_file(path: Path) -> str | Deny:
    """Mode and type are read from the open descriptor, so they describe the bytes read."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return Deny(DENY_OV_SERVICE_TOKEN_MISSING)
    except OSError:
        return Deny(DENY_OV_SERVICE_TOKEN_UNREADABLE)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return Deny(DENY_OV_SERVICE_TOKEN_NOT_FILE)
        if stat.S_IMODE(st.st_mode) != _OV_SERVICE_TOKEN_MODE:
            return Deny(DENY_OV_SERVICE_TOKEN_BAD_MODE)
        raw = os.read(fd, _OV_SERVICE_TOKEN_MAX_BYTES + 1)
    except OSError:
        return Deny(DENY_OV_SERVICE_TOKEN_UNREADABLE)
    finally:
        os.close(fd)
    if len(raw) > _OV_SERVICE_TOKEN_MAX_BYTES:
        return Deny(DENY_OV_SERVICE_TOKEN_INVALID)
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return Deny(DENY_OV_SERVICE_TOKEN_INVALID)


def ov_service_token() -> str | Deny:
    """Cortex's own OpenVault service bearer from its 0600 secret file. Never the admin token."""
    raw = (os.environ.get(OV_SERVICE_TOKEN_FILE_ENV) or "").strip()
    if not raw:
        return Deny(DENY_OV_SERVICE_TOKEN_MISSING)
    path = Path(raw).expanduser()
    # Refused by name only. Cortex never reads, locates or falls back to the admin token.
    if path.name == _OV_ADMIN_TOKEN_FILENAME:
        return Deny(DENY_OV_SERVICE_TOKEN_IS_ADMIN)
    token = _read_service_token_file(path)
    if isinstance(token, Deny):
        return token
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
    if status == 429:
        return _ov_deny(DENY_OV_VERIFY_RATE_LIMITED, status)
    if 500 <= status <= 599:
        return _ov_deny(DENY_OV_VERIFY_SERVER_ERROR, status)
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

    # Header only. The cortex_api_key cookie is read by constructor routes,
    # as on parent. Accepting it here would open every get_caller route.
    key = extract_api_key(x_api_key, authorization)
    decision = resolve_auth(key)
    if isinstance(decision, Deny):
        raise HTTPException(
            status_code=401,
            detail=f"Valid API key required (X-API-Key or Bearer): {decision.reason}",
        )
    return decision


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
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> Caller:
    """Same key store as ``get_caller``, without the ``DMS_AUTH_DISABLED`` short-circuit.

    Routes that did not call ``get_caller`` on parent must not start honoring
    that flag. Honoring it would open them as admin.
    """
    key = extract_api_key(x_api_key, authorization)
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
