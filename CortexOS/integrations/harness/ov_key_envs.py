"""Provider key env names OpenVault custodies: a read-only mirror, not a registry.

OpenVault owns the provider catalogue (ids, base URLs, models, which env name
maps to which provider) and every provider key. Cortex needs only the names,
so that no child process it spawns inherits one. :data:`OV_KEY_ENVS` is
OpenVault's ``env_ingest.known_env_keys()`` minus ``NON_SECRET_ENV_KEYS`` at
:data:`OV_PIN`. OpenVault serves no endpoint with these names (its
``/api/keyvault/snapshot`` gives one ``env_key`` per catalogue row, no
aliases), so ``tests/harness/test_ov_key_envs.py`` re-derives the set from an
OpenVault checkout and fails on drift.

Nothing here routes a model call or reads a key value.
"""

from __future__ import annotations

OV_REPO = "Netie-AI/OpenVault"
OV_PIN = "b4d68021aef4a172dc26a2598d287e217cbd2d03"
#: Where OpenVault defines the names, relative to its repo root.
OV_SOURCES: dict[str, str] = {
    "PROVIDER_TO_ENV": "OpenMW/openmw/openvault/vault/airgpt_keyvault.py",
    "ENV_KEY_TO_PROVIDER": "OpenMW/openmw/openvault/vault/airgpt_keyvault.py",
    "CF_TOKEN_ENV_KEYS": "OpenMW/openmw/openvault/vault/free_keys_onboard.py",
    "NON_SECRET_ENV_KEYS": "OpenMW/openmw/openvault/vault/env_ingest.py",
}

OV_KEY_ENVS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "CEREBRAS_API_KEY",
    "CF_API_TOKEN",
    "CLOUDFLARE_API_TOKEN",
    "CURSOR_API_KEY",
    "DEEPGRAM_API_KEY",
    "DEEPSEEK_API_KEY",
    "FIREWORKS_API_KEY",
    "FREENVIDIA_API_KEY",
    "GEMINI_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GOOGLE_AISTUDIO_FREE",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "HF_TOKEN",
    "HUGGINGFACE_API_KEY",
    "HUGGING_FACE_HUB_TOKEN",
    "LITELLM_API_KEY",
    "MISTRAL_API_KEY",
    "NETIE_ENGINE_KEY",
    "NVIDIA_API_KEY",
    "NVIDIA_NIM_API_KEY",
    "OLLAMA_API_KEY",
    "OMNIROUTE_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENSHIP_API_TOKEN",
    "SEA_LION_API_KEY",
    "SILICONFLOW_API_KEY",
    "TOGETHER_API_KEY",
    "VLLM_API_KEY",
)

__all__ = ["OV_KEY_ENVS", "OV_PIN", "OV_REPO", "OV_SOURCES"]
