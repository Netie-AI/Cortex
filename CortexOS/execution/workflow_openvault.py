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
)
from CortexOS.integrations.openvault_client import get_json as _get_json
from CortexOS.integrations.openvault_client import (
    ping as _ping,
)

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

#: The only env names a keyvault snapshot may write. A snapshot row naming any
#: other ``env`` (``CORTEX_MODEL_TRANSPORT``, ``PATH``, ``CORTEX_FREEROUTE``...)
#: would reconfigure this process, not hydrate a key, so it is refused.
ALLOWED_KEY_ENVS: frozenset[str] = frozenset(_KEY_ENV.values())
REFUSED_ENV_NOTE = "not a known provider key env; snapshot may only hydrate provider keys"


def workflow_identity(run_id: str, node_id: str = "") -> str:
    rid = (run_id or "anon").replace(" ", "")[:64]
    nid = (node_id or "").replace(" ", "")[:64]
    return f"wf:{rid}:{nid}" if nid else f"wf:{rid}"


def ping() -> bool:
    return _ping()


def ensure_provider_keys(*, force: bool = False) -> dict[str, Any]:
    """Hydrate missing provider env vars from OpenVault when online.

    Returns a status dict suitable for diagnostics (no secret material).
    Only :data:`ALLOWED_KEY_ENVS` are ever written; any other env name a
    snapshot row carries is listed under ``refused`` with a named note.
    """
    snap = _get_json("/api/keyvault/snapshot")
    if not snap or not snap.get("ok"):
        return {"ok": False, "source": "offline", "hydrated": []}

    hydrated: list[str] = []
    refused: list[dict[str, str]] = []
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
        if env_name not in ALLOWED_KEY_ENVS:
            refused.append({"env": env_name[:64], "note": REFUSED_ENV_NOTE})
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

    return {"ok": True, "source": "openvault", "hydrated": hydrated, "refused": refused}


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
