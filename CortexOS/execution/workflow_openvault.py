"""OpenVault key + FreeRoute identity helpers for workflow LLM spend.

Keys: prefer OpenVault snapshot when reachable; otherwise leave process env
alone (AirGPT/env.local already mirrored). Never log secret values.

Identity: ``wf:{run_id}:{node_id}`` so FreeRoute ratelimit/settle can attribute
massive multi-agent consumption to the right background run.

Uses ``CortexOS.integrations.openvault_client`` for resilient path selection
(canonical FreeRoute + legacy OpenFree alias).
"""

from __future__ import annotations

import os
from typing import Any

from CortexOS.integrations.openvault_client import (
    freeroute_ratelimit,
    openvault_base_url,
    ping as _ping,
)
from CortexOS.integrations.openvault_client import get_json as _get_json

# Re-export for callers that read the module constant.
OPENVAULT_URL = os.environ.get("OPENVAULT_URL", openvault_base_url())

#: Catalog id → env var the LiteLLM / adapters already read.
_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "together": "TOGETHER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


def workflow_identity(run_id: str, node_id: str = "") -> str:
    rid = (run_id or "anon").replace(" ", "")[:64]
    nid = (node_id or "").replace(" ", "")[:64]
    return f"wf:{rid}:{nid}" if nid else f"wf:{rid}"


def ping() -> bool:
    return _ping()


def ensure_provider_keys(*, force: bool = False) -> dict[str, Any]:
    """Hydrate missing provider env vars from OpenVault when online.

    Returns a status dict suitable for diagnostics (no secret material).
    """
    snap = _get_json("/api/keyvault/snapshot")
    if not snap or not snap.get("ok"):
        return {"ok": False, "source": "offline", "hydrated": []}

    hydrated: list[str] = []
    providers = snap.get("providers") or snap.get("secrets") or []
    if isinstance(providers, dict):
        iterable = [
            {"id": k, **(v if isinstance(v, dict) else {"value": v})}
            for k, v in providers.items()
        ]
    else:
        iterable = list(providers)

    for item in iterable:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or item.get("provider") or "").lower()
        env_name = _KEY_ENV.get(pid) or str(item.get("env") or "")
        if not env_name:
            continue
        if os.environ.get(env_name) and not force:
            continue
        secret = item.get("value") or item.get("secret") or item.get("key")
        if not secret or not isinstance(secret, str):
            continue
        if set(secret) <= {"*", "•", "x", "X"} or secret.startswith("••••"):
            continue
        os.environ[env_name] = secret
        hydrated.append(env_name)

    return {"ok": True, "source": "openvault", "hydrated": hydrated}


def check_openfree_budget(
    identity: str,
    *,
    prompt_tokens: int = 0,
    max_tokens: int = 0,
    tier: str = "free",
) -> dict[str, Any]:
    """Soft pre-check against FreeRoute ratelimit. Never blocks when OV is down."""
    data = freeroute_ratelimit(identity, tier=tier)
    if not data:
        return {"ok": True, "skipped": True, "reason": "openvault_unreachable"}
    remaining = data.get("remaining_tokens")
    if remaining is None:
        remaining = data.get("remaining")
    projected = int(prompt_tokens or 0) + int(max_tokens or 0)
    if remaining is not None and projected > int(remaining):
        return {
            "ok": False,
            "skipped": False,
            "remaining": int(remaining),
            "projected": projected,
            "error": f"FreeRoute budget {remaining} tokens left; need ~{projected}",
        }
    return {"ok": True, "skipped": False, "remaining": remaining, "identity": identity}
