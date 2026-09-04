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
    "GOOGLE_API_KEY",
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
    "CREW_OPENVAULT_MODEL",
    "GMAIL_IMAP_USER",
    "GMAIL_APP_PASSWORD",
    "GMAIL_IMAP_HOST",
)


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
            current[key] = str(value).strip()
            os.environ[key] = current[key]
            if key.endswith("_API_KEY"):
                from CortexOS.crew.openvault import upsert_env_key

                vaulted = upsert_env_key(key, current[key])
                if not vaulted.get("ok"):
                    os.environ["CREW_VAULT_LAST_ERROR"] = str(vaulted.get("detail") or "vault upsert failed")
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
        {"key": "GOOGLE_API_KEY", "label": "Google AI", "hint": "AIza..."},
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
