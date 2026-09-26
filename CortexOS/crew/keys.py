"""Operator-supplied provider keys, stored under data/crew/ (gitignored).

Env vars always win over the file so a shell-exported key is never overwritten
on boot. Saving from the UI writes the file *and* updates this process so the
next chat uses the new key without a restart. GET never returns secret values.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

KNOWN = (
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "CURSOR_API_KEY",
    "XAI_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "NVIDIA_API_KEY",
    "CEREBRAS_API_KEY",
    "MISTRAL_API_KEY",
    "CREW_MODEL",
    "CREW_PROVIDER",
    "CREW_OPENAI_BASE_URL",
    "CREW_CURSOR_BASE_URL",
    "CREW_ANTHROPIC_MODEL",
    "CREW_OPENROUTER_MODEL",
    "CREW_DEEPSEEK_MODEL",
    "CREW_OPENAI_MODEL",
    "CREW_CURSOR_MODEL",
    "CREW_XAI_MODEL",
    "CREW_GOOGLE_MODEL",
    "CREW_NVIDIA_MODEL",
    "CREW_NVIDIA_BASE_URL",
    "CREW_CEREBRAS_MODEL",
    "CREW_OPENVAULT_MODEL",
    "GMAIL_IMAP_USER",
    "GMAIL_APP_PASSWORD",
    "GMAIL_IMAP_HOST",
)

# Env names a litellm-routed host reads, first set wins. The provider chain
# stamps that name as the source and ``llm.chat`` spends that same key, so the
# stamp can never name one key while another is spent. litellm alone reads
# GOOGLE_API_KEY before GEMINI_API_KEY, and NVIDIA_NIM_API_KEY, never
# NVIDIA_API_KEY.
KEY_ENVS: dict[str, tuple[str, ...]] = {
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "nvidia": ("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"),
}
# litellm model prefix -> chain label whose KEY_ENVS key ``llm.chat`` passes.
KEY_PREFIXES: dict[str, str] = {"gemini": "google", "nvidia_nim": "nvidia"}


def key_env(label: str) -> str:
    """Name of the first set env var for ``label``, or ''. Never the value."""
    for name in KEY_ENVS.get(label, ()):
        if os.environ.get(name, "").strip():
            return name
    return ""


def _path(data_dir: Path) -> Path:
    return data_dir / "keys.json"


def load_saved(data_dir: Path) -> dict[str, str]:
    path = _path(data_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key in KNOWN and isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def apply_saved(data_dir: Path) -> None:
    """Fill empty env slots from the saved file. Does not clobber a live env."""
    for key, value in load_saved(data_dir).items():
        if not os.environ.get(key):
            os.environ[key] = value


def save(data_dir: Path, updates: dict[str, str | None]) -> dict[str, Any]:
    """Merge updates into the file and this process. Empty string unsets."""
    current = load_saved(data_dir)
    for key, value in updates.items():
        if key not in KNOWN:
            continue
        if value is None or not str(value).strip():
            current.pop(key, None)
            os.environ.pop(key, None)
        else:
            stripped = str(value).strip()
            os.environ[key] = stripped
            keep_local = True
            if key.endswith("_API_KEY"):
                from CortexOS.crew.openvault import upsert_env_key

                vaulted = upsert_env_key(key, stripped)
                if vaulted.get("ok"):
                    # Secret lives in OpenVault. Do not keep a crew copy on disk.
                    keep_local = False
                    os.environ.pop("CREW_VAULT_LAST_ERROR", None)
                else:
                    os.environ["CREW_VAULT_LAST_ERROR"] = str(
                        vaulted.get("detail") or "vault upsert failed"
                    )
            if keep_local:
                current[key] = stripped
            else:
                current.pop(key, None)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _path(data_dir)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return status()


def status() -> dict[str, Any]:
    fields: dict[str, dict[str, bool]] = {}
    for key in KNOWN:
        fields[key] = {"configured": bool(os.environ.get(key))}
    return {"fields": fields}


def public_fields() -> list[dict[str, str]]:
    """UI form labels; never includes values."""
    return [
        {"key": "ANTHROPIC_API_KEY", "label": "Anthropic", "hint": "sk-ant-..."},
        {"key": "OPENROUTER_API_KEY", "label": "OpenRouter", "hint": "sk-or-..."},
        {"key": "DEEPSEEK_API_KEY", "label": "DeepSeek", "hint": "cheap default"},
        {"key": "OPENAI_API_KEY", "label": "OpenAI / compatible", "hint": "sk-..."},
        {"key": "CREW_OPENAI_BASE_URL", "label": "OpenAI-compatible base URL", "hint": "http://host/v1"},
        {"key": "CURSOR_API_KEY", "label": "Cursor", "hint": "cursor api key (routes grok-4.6 high, not fast)"},
        {"key": "CREW_CURSOR_MODEL", "label": "Cursor model", "hint": "grok-4.6"},
        {"key": "GMAIL_IMAP_USER", "label": "Gmail IMAP user", "hint": "you@gmail.com"},
        {"key": "GMAIL_APP_PASSWORD", "label": "Gmail app password", "hint": "IMAP read; Crew never sends"},
        {"key": "GROQ_API_KEY", "label": "Groq", "hint": "gsk_... (vaulted, used via OpenVault)"},
        {"key": "GEMINI_API_KEY", "label": "Google AI (Gemini)", "hint": "AIza... (read before GOOGLE_API_KEY)"},
        {"key": "GOOGLE_API_KEY", "label": "Google AI (alt name)", "hint": "AIza... (used when GEMINI_API_KEY is unset)"},
        {"key": "NVIDIA_API_KEY", "label": "NVIDIA NIM", "hint": "nvapi-... (integrate.api.nvidia.com)"},
        {"key": "CREW_NVIDIA_MODEL", "label": "NVIDIA NIM model", "hint": "moonshotai/kimi-k3"},
        {"key": "CEREBRAS_API_KEY", "label": "Cerebras", "hint": "csk-..."},
        {"key": "MISTRAL_API_KEY", "label": "Mistral", "hint": "vaulted via OpenVault"},
        {"key": "XAI_API_KEY", "label": "xAI / Grok", "hint": "xai-..."},
        {"key": "CREW_PROVIDER", "label": "Pinned provider", "hint": "openvault | anthropic | groq | openrouter | ..."},
        {"key": "CREW_MODEL", "label": "Explicit model override", "hint": "openrouter/deepseek/deepseek-chat"},
    ]


def pin_provider(
    data_dir: Path, provider: str | None, model: str | None = None
) -> dict[str, Any]:
    """Persist the operator's host pick. Empty unpins. Never stores secrets."""
    updates: dict[str, str | None] = {}
    if provider is not None:
        updates["CREW_PROVIDER"] = (provider or "").strip() or None
    if model is not None:
        updates["CREW_MODEL"] = (model or "").strip() or None
    return save(data_dir, updates)
