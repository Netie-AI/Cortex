"""Shared OpenVault HTTP client — resilient paths, no secrets in logs.

Cortex is a *consumer* of OpenVault (PRODUCT_ROLES): keys, gate, FreeRoute budget,
and access routing. Memory stays on Cortex ``/api/memory/*``; OpenVault only
signposts it via ``/api/access/resolve``.

Canonical paths are tried first; legacy aliases are fallbacks so older installs
and half-upgraded stacks still work.
"""

from __future__ import annotations

import ipaddress
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

DEFAULT_OPENVAULT_URL = "http://127.0.0.1:5000"

# Engine names first; Crew's historical name last. One OpenVault per Cortex.
_BASE_URL_ENVS: tuple[str, ...] = ("OPENVAULT_BASE_URL", "OPENVAULT_URL", "CREW_OPENVAULT_URL")

# Canonical first, legacy alias second — see OpenVault rename (FreeRoute/FreeBuild/FreeIDE).
_RATELIMIT_PATHS: tuple[str, ...] = (
    "/api/freeroute/ratelimit",
    "/api/openfree/ratelimit",
)
_HEALTH_PATHS: tuple[str, ...] = (
    "/api/healthz",
    "/api/keyvault/snapshot",
    "/api/access/uptime",
)


def openvault_base_url() -> str:
    for name in _BASE_URL_ENVS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value.rstrip("/")
    return DEFAULT_OPENVAULT_URL


def openvault_base_url_conflict() -> str:
    """Name the variables when two OpenVault URL aliases disagree; '' when they agree.

    Engine and Crew used to read different names. Two vaults behind one Cortex
    would split arming from spend, so the disagreement is refused, not resolved.
    """
    seen: dict[str, str] = {}
    for name in _BASE_URL_ENVS:
        value = (os.environ.get(name) or "").strip()
        if value:
            seen[name] = value.rstrip("/")
    if len(set(seen.values())) <= 1:
        return ""
    named = ", ".join(f"{name}={value}" for name, value in seen.items())
    return f"OpenVault URL variables disagree ({named}); set one OpenVault"


def is_loopback_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _json_dict(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def request_json(
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 5.0,
    base: str | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """HTTP JSON with the status code; ``(0, None)`` when OpenVault is unreachable.

    Error bodies are parsed too, so callers name a 401 / 403 / 429 instead of
    collapsing every refusal into "unreachable". Loopback bases skip proxies:
    a system proxy must never receive a bearer meant for 127.0.0.1. Never raises.
    """
    root = base or openvault_base_url()
    url = f"{root}{path}"
    payload = json.dumps(dict(body)).encode("utf-8") if body is not None else None
    sent = {"Accept": "application/json"}
    if payload is not None:
        sent["Content-Type"] = "application/json"
    sent.update(dict(headers or {}))
    try:
        req = urllib.request.Request(url, data=payload, method=method.upper(), headers=sent)
        if is_loopback_url(root):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            opener = urllib.request.build_opener()
        with opener.open(req, timeout=timeout) as resp:
            return int(resp.status), _json_dict(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except OSError:
            raw = b""
        return int(exc.code), _json_dict(raw)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None


def get_json(
    path: str,
    *,
    timeout: float = 1.2,
    base: str | None = None,
) -> dict[str, Any] | None:
    """GET JSON; return None on any failure (never raises)."""
    url = f"{base or openvault_base_url()}{path}"
    try:
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None


def post_json(
    path: str,
    body: Mapping[str, Any],
    *,
    timeout: float = 3.0,
    base: str | None = None,
) -> dict[str, Any] | None:
    """POST JSON; return None on failure."""
    url = f"{base or openvault_base_url()}{path}"
    try:
        payload = json.dumps(dict(body)).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None


def get_json_first(
    paths: tuple[str, ...],
    *,
    timeout: float = 1.2,
    base: str | None = None,
) -> dict[str, Any] | None:
    """Try paths in order; first successful JSON dict wins."""
    root = base or openvault_base_url()
    for path in paths:
        data = get_json(path, timeout=timeout, base=root)
        if data is not None:
            return data
    return None


def ping(*, timeout: float = 1.2) -> bool:
    """True when OpenVault answers on any known health path."""
    data = get_json_first(_HEALTH_PATHS, timeout=timeout)
    if not data:
        return False
    if data.get("status") == "ok":
        return True
    if data.get("ok") is True:
        return True
    if "entries" in data or "surfaces" in data:
        return True
    return False


def freeroute_ratelimit(
    identity: str,
    *,
    tier: str = "free",
    timeout: float = 1.2,
) -> dict[str, Any] | None:
    q = urllib.parse.urlencode({"identity": identity, "tier": tier})
    return get_json_first(
        tuple(f"{p}?{q}" for p in _RATELIMIT_PATHS),
        timeout=timeout,
    )


def resolve_access(
    kind: str,
    entry_id: str,
    intent: str = "read",
    *,
    timeout: float = 2.0,
) -> dict[str, Any]:
    """POST /api/access/resolve — location + gate verdict, never content."""
    data = post_json(
        "/api/access/resolve",
        {"kind": kind, "id": entry_id, "intent": intent},
        timeout=timeout,
    )
    if data is not None:
        return data
    return {
        "found": False,
        "allowed": False,
        "reasons": ["OpenVault unreachable or /api/access/resolve unavailable"],
    }


def memory_route(*, intent: str = "read") -> dict[str, Any]:
    """Where Cortex memory lives — OpenVault routes, Cortex stores."""
    return resolve_access("memory", "cortex.memory", intent=intent)


__all__ = [
    "DEFAULT_OPENVAULT_URL",
    "freeroute_ratelimit",
    "get_json",
    "get_json_first",
    "is_loopback_url",
    "memory_route",
    "openvault_base_url",
    "openvault_base_url_conflict",
    "ping",
    "post_json",
    "request_json",
    "resolve_access",
]
