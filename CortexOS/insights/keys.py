"""Local vs cloud key posture and A-0009 caller bearer for /v1/insights.

Consumes CortexOS.integrations.freeroute (arming, identity, peek). Does not
rewrite that layer. Never returns tokens or provider secrets.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from CortexOS.integrations import freeroute as core
from CortexOS.integrations import openvault_client

_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "localhost"})
_LOCAL_HOP_PROVIDERS = frozenset(
    {
        "ollama",
        "local",
        "loopback",
        "this-pc",
        "llama.cpp",
        "lmstudio",
        "vllm",
    }
)

CALLER_KEY_RULE = (
    "FreeRoute spend from a non-loopback caller needs its own OpenVault ov_ key "
    "(Authorization: Bearer ov_...)"
)


def peer_is_loopback(host: str) -> bool:
    """Only the socket peer counts. Forwarded headers are caller-written.

    Starlette TestClient peer ``testclient`` is not an address, so it does not
    prove loopback (OpenVault applies the same rule to its own callers).
    """
    raw = (host or "").strip()
    if raw in _LOOPBACK_PEERS:
        return True
    try:
        return ipaddress.ip_address(raw).is_loopback
    except ValueError:
        return False


def relay_bearer(authorization: str, peer_host: str) -> str | None:
    """Credential a FreeRoute spend is relayed with. ``None`` refuses (A-0009).

    A caller's own ``Bearer ov_...`` is relayed verbatim from any peer. Without
    one, only a loopback peer talking to a loopback OpenVault proceeds, with no
    Authorization at all. Cortex's own key is never lent.
    """
    auth = (authorization or "").strip()
    bearer = auth[len("bearer ") :].strip() if auth.lower().startswith("bearer ") else ""
    if bearer.startswith("ov_"):
        return bearer
    if peer_is_loopback(peer_host) and openvault_client.is_loopback_url(
        openvault_client.openvault_base_url()
    ):
        return ""
    return None


def key_posture() -> dict[str, Any]:
    """Local vs cloud key posture from the current FreeRoute arming snapshot."""
    snap = core.arming()
    local: list[str] = []
    cloud: list[str] = []
    for hop in snap.hops_public:
        provider = str(hop.get("provider") or "").strip().lower()
        if not provider or provider == "cortex":
            continue
        if provider in _LOCAL_HOP_PROVIDERS:
            if provider not in local:
                local.append(provider)
        elif provider not in cloud:
            cloud.append(provider)
    cred = core.identity()
    return {
        "ok": True,
        "custody": "openvault",
        "second_vault": False,
        "callers_hold_provider_keys": False,
        "live_key_rotate": False,
        "live_5000_ci": False,
        "vault_url_loopback": openvault_client.is_loopback_url(snap.url),
        "credential_mode": cred.get("mode"),
        "armed": snap.armed,
        "reason": snap.reason,
        "probed": snap.probed,
        "local_hops": list(local),
        "cloud_hops": list(cloud),
        "local_keys": bool(local),
        "cloud_keys": bool(cloud),
        "sealed": snap.sealed,
        "pooled_keys": snap.pooled_keys,
        "spendable_hops": snap.spendable_hops,
        "token_returned": False,
        "issue_with": cred.get("issue_with"),
        "note": (
            "Local hops are OpenVault-custodied ground models. Cloud hops are "
            "OpenVault-custodied provider APIs. Cortex never stores provider "
            "secrets. Callers present ov_ or loopback; they do not present "
            "raw provider keys. This payload never rotates or pastes a live "
            "provider key. live_5000_ci is always false."
        ),
    }
