"""Issue and verify OpenVault ``ov_`` keys. Cortex never stores the token."""

from __future__ import annotations

from typing import Any

from CortexOS.integrations.openvault_client import post_json


def verify_token(token: str) -> dict[str, Any] | None:
    """POST /api/apikeys/verify. None when OpenVault is down or the token is bad."""
    token = (token or "").strip()
    if not token.startswith("ov_"):
        return None
    data = post_json("/api/apikeys/verify", {"token": token}, timeout=3.0)
    if data and data.get("ok") is True and isinstance(data.get("key"), dict):
        return data
    return None


def issue_token(label: str = "constructor", tier: str = "free") -> dict[str, Any] | None:
    """POST /api/apikeys. Token is shown once. None when OpenVault is unreachable."""
    return post_json(
        "/api/apikeys",
        {"label": label, "tier": tier},
        timeout=5.0,
    )
