"""OpenVault gate client — Cortex asks; OpenVault allows (PRODUCT_ROLES).

No local keystore. Keys and leave-machine permission live in OpenVault.
"""

from __future__ import annotations

from typing import Any

from CortexOS.integrations.openvault_client import get_json, openvault_base_url, post_json


def check_gate(
    *,
    action: str = "run",
    project_path: str = "",
    destination: str = "",
    required_providers: list[str] | None = None,
) -> dict[str, Any]:
    """POST /api/gate/check — deny-by-default if OpenVault is offline."""
    base = openvault_base_url()
    payload = {
        "action": action,
        "project_path": project_path,
        "destination": destination,
        "required_providers": required_providers or [],
    }
    try:
        out = post_json("/api/gate/check", payload, timeout=5.0, base=base) or {}
        if not out:
            raise OSError("empty response")
        out["openvault_url"] = base
        out["ok"] = True
        return out
    except OSError as e:
        return {
            "ok": False,
            "allowed": False,
            "action": action,
            "openvault_url": base,
            "reasons": [f"OpenVault unreachable: {str(e)[:160]}"],
            "keys_ready": False,
        }


def resolve_keyvault_snapshot() -> dict[str, Any]:
    """Read-only catalog from OpenVault (Cortex never stores secrets)."""
    base = openvault_base_url()
    try:
        snap = get_json("/api/keyvault/snapshot", timeout=5.0, base=base) or {}
        if not snap:
            raise OSError("empty snapshot")
        snap["ok"] = True
        snap["openvault_url"] = base
        return snap
    except OSError as e:
        return {"ok": False, "openvault_url": base, "error": str(e)[:200]}
