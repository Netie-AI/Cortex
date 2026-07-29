"""Shared OpenVault HTTP client — resilient paths, no secrets in logs.

Cortex is a *consumer* of OpenVault (PRODUCT_ROLES): keys, gate, FreeRoute budget,
and access routing. Memory stays on Cortex ``/api/memory/*``; OpenVault only
signposts it via ``/api/access/resolve``.

Canonical paths are tried first; legacy aliases are fallbacks so older installs
and half-upgraded stacks still work.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

DEFAULT_OPENVAULT_URL = "http://127.0.0.1:5000"

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
    return (
        os.environ.get("OPENVAULT_BASE_URL")
        or os.environ.get("OPENVAULT_URL")
        or DEFAULT_OPENVAULT_URL
    ).rstrip("/")


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
    "memory_route",
    "openvault_base_url",
    "ping",
    "post_json",
    "resolve_access",
]
