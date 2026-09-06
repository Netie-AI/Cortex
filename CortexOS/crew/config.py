"""Crew configuration - provider-agnostic model resolution.

The crew layer never requires one specific model host. Resolution walks a
chain (explicit ``CREW_MODEL``, then live OpenVault FreeRoute, then Anthropic,
Cursor, OpenRouter, DeepSeek, any OpenAI-compatible endpoint, Groq / Google /
Cerebras / Mistral, and last a locally reachable Ollama).

``CREW_PROVIDER`` pins one label. A pin does not fall through to the next
configured host if that label is unset or down. The winner is stamped on
every reply envelope.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path

from CortexOS.paths import data_path

DEFAULT_PORT = 8020  # 8010 is the engine, 8765 is reserved for AirGPT.


@dataclass(frozen=True)
class Provider:
    label: str
    model: str
    source: str
    configured: bool
    active: bool = False
    api_base: str | None = None
    connector: str = "litellm"
    armed_via: str = ""

    def public(self) -> dict[str, object]:
        return {
            "label": self.label,
            "model": self.model,
            "source": self.source,
            "configured": self.configured,
            "active": self.active,
            "armed": self.configured,
            "connector": self.connector,
            "armed_via": self.armed_via,
        }


def _flag(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


# Operator-chosen exec plane. Distinct from CREW_RUNTIME (path to RUNTIME.md).
BACKEND_LAPTOP = "laptop"
BACKEND_CF_COMPUTER = "cloudflare-computer"
RUNTIME_BACKENDS = frozenset({BACKEND_LAPTOP, BACKEND_CF_COMPUTER})
# Isolate path is PREVIEW-only (https://github.com/cloudflare/computer). Off by default.
FLAG_CF_COMPUTER = "CREW_CF_COMPUTER"


def parse_runtime_backend(raw: str | None) -> str:
    value = (raw or "").strip().lower().replace("_", "-")
    if value in {"cf", "cf-computer", "cloudflare", "isolate", "computer"}:
        value = BACKEND_CF_COMPUTER
    if value in RUNTIME_BACKENDS:
        return value
    return BACKEND_LAPTOP


@dataclass
class CrewSettings:
    port: int
    engine_url: str
    engine_session: str
    data_dir: Path
    master_computer_control: bool
    ui_dir: Path = field(default_factory=lambda: Path(__file__).parent / "ui")
    max_agents_per_space: int = 8
    max_llm_calls_per_run: int = 40
    max_steps_per_agent: int = 12
    confirm_timeout_s: int = 300
    llm_timeout_s: int = 180
    runtime_backend: str = BACKEND_LAPTOP
    cf_computer_enabled: bool = False
    cf_computer_url: str = ""
    cf_computer_token: str = ""
    cf_computer_forward_host_env: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "crew.db"

    @property
    def mcp_config_path(self) -> Path:
        return self.data_dir / "mcp_servers.json"


def load_settings() -> CrewSettings:
    data_dir = Path(os.environ.get("CREW_DATA_DIR") or data_path("crew"))
    data_dir.mkdir(parents=True, exist_ok=True)
    from CortexOS.crew.keys import apply_saved

    apply_saved(data_dir)
    return CrewSettings(
        port=int(os.environ.get("CREW_PORT", str(DEFAULT_PORT))),
        engine_url=os.environ.get("CREW_ENGINE_URL", "http://127.0.0.1:8010").rstrip("/"),
        # "demo" is the engine's bound demo session; unbound sessions abstain
        # by design (ANS work), and crew renders that abstention honestly.
        engine_session=os.environ.get("CREW_ENGINE_SESSION", "demo"),
        data_dir=data_dir,
        master_computer_control=_flag("CORTEX_COMPUTER_CONTROL"),
        runtime_backend=parse_runtime_backend(os.environ.get("CREW_RUNTIME_BACKEND")),
        cf_computer_enabled=_flag(FLAG_CF_COMPUTER),
        cf_computer_url=os.environ.get("CREW_CF_COMPUTER_URL", "").strip(),
        cf_computer_token=os.environ.get("CREW_CF_COMPUTER_TOKEN", "").strip(),
        cf_computer_forward_host_env=_flag("CREW_CF_COMPUTER_FORWARD_HOST_ENV"),
    )


_OLLAMA_CACHE: tuple[float, str | None] = (0.0, None)


def _ollama_first_model(base_url: str, timeout: float = 0.8) -> str | None:
    """Return the first locally available Ollama model tag, cached for 60s.

    Ollama is optional and last in the chain - never required, never probed
    more than once a minute.
    """
    global _OLLAMA_CACHE
    ts, cached = _OLLAMA_CACHE
    if time.monotonic() - ts < 60.0:
        return cached
    found: str | None = None
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as resp:
            tags = json.loads(resp.read().decode("utf-8", "replace"))
        models = tags.get("models") or []
        if models:
            found = str(models[0].get("name") or "") or None
    except Exception:
        found = None
    _OLLAMA_CACHE = (time.monotonic(), found)
    return found


def _arm_flags(
    label: str, env_ok: bool, ov_ok: bool, vault: dict[str, dict]
) -> tuple[bool, str, str]:
    """Return configured, armed_via, connector. FreeRoute wins when measured live."""
    vault_ok = bool(vault.get(label) and vault[label].get("enabled"))
    configured = bool(env_ok or vault_ok)
    if env_ok and vault_ok:
        via = "both"
    elif vault_ok:
        via = "openvault"
    elif env_ok:
        via = "env"
    else:
        via = ""
    prefer_ov = bool(ov_ok and vault_ok)
    connector = "openvault" if (label == "openvault" or prefer_ov) else "litellm"
    return configured, via, connector


def _wrap_freeroute(label: str, model: str, connector: str) -> str:
    if connector != "openvault" or label in {"openvault", "explicit"}:
        return model
    if model.startswith("openvault/"):
        return model
    if label == "cursor":
        from CortexOS.crew.openvault import cursor_model

        return "openvault/" + cursor_model()
    return "openvault/" + model


def resolve_providers() -> list[Provider]:
    """Build the provider chain.

    Default: the first configured entry is active. When ``CREW_PROVIDER`` is
    set, only that label may be active; a miss stays inactive (no fallback).
    A host is configured if its env key is set *or* OpenVault lists an enabled
    key for that label. Vault-armed hosts route through FreeRoute when the
    vault is live; crew does not need a local copy of the secret.
    """
    env = os.environ
    chain: list[Provider] = []
    ov_ok = False
    vault: dict[str, dict] = {}
    if env.get("CREW_OPENVAULT", "1") != "0":
        from CortexOS.crew.openvault import healthz, vault_sources

        ov_ok = bool(healthz().get("ok"))
        vault = vault_sources()

    def add(
        label: str,
        model: str,
        env_source: str,
        env_ok: bool,
        api_base: str | None = None,
        *,
        connector: str | None = None,
        via: str | None = None,
        configured: bool | None = None,
    ) -> None:
        cfg, armed_via, conn = _arm_flags(label, env_ok, ov_ok, vault)
        if configured is not None:
            cfg = configured
        if connector is not None:
            conn = connector
        if via is not None:
            armed_via = via
        source = env_source
        if conn == "openvault" and label != "explicit":
            source = "OpenVault FreeRoute (vault-armed)" if label != "openvault" else env_source
        chain.append(
            Provider(
                label=label,
                model=_wrap_freeroute(label, model, conn),
                source=source,
                configured=cfg,
                api_base=api_base,
                connector=conn,
                armed_via=armed_via,
            )
        )

    explicit = env.get("CREW_MODEL", "").strip()
    add(
        "explicit",
        explicit or "(set CREW_MODEL to any litellm model string)",
        "CREW_MODEL",
        bool(explicit),
        connector="openvault" if explicit.startswith("openvault/") else "litellm",
        via="env" if explicit else "",
        configured=bool(explicit),
    )
    add(
        "openvault",
        "openvault/" + (env.get("CREW_OPENVAULT_MODEL", "").strip() or "auto"),
        "OpenVault FreeRoute (loopback, keys stay in the vault)",
        ov_ok,
        env.get("CREW_OPENVAULT_URL", "http://127.0.0.1:5000"),
        connector="openvault",
        via="openvault" if ov_ok else "",
        configured=ov_ok,
    )
    add(
        "anthropic",
        "anthropic/" + env.get("CREW_ANTHROPIC_MODEL", "claude-sonnet-5"),
        "ANTHROPIC_API_KEY",
        bool(env.get("ANTHROPIC_API_KEY")),
    )
    cursor_base = env.get("CREW_CURSOR_BASE_URL", "https://api.cursor.com/v1").strip() or None
    add(
        "cursor",
        "openai/" + env.get("CREW_CURSOR_MODEL", "grok-4.6"),
        "CURSOR_API_KEY",
        bool(env.get("CURSOR_API_KEY")),
        cursor_base,
    )
    add(
        "openrouter",
        "openrouter/" + env.get("CREW_OPENROUTER_MODEL", "deepseek/deepseek-chat"),
        "OPENROUTER_API_KEY",
        bool(env.get("OPENROUTER_API_KEY")),
    )
    add(
        "xai",
        "xai/" + env.get("CREW_XAI_MODEL", "grok-4"),
        "XAI_API_KEY",
        bool(env.get("XAI_API_KEY")),
    )
    add(
        "deepseek",
        "deepseek/" + env.get("CREW_DEEPSEEK_MODEL", "deepseek-chat"),
        "DEEPSEEK_API_KEY",
        bool(env.get("DEEPSEEK_API_KEY")),
    )
    openai_base = env.get("CREW_OPENAI_BASE_URL", "").strip() or None
    add(
        "openai-compatible",
        "openai/" + env.get("CREW_OPENAI_MODEL", "gpt-4o-mini"),
        "OPENAI_API_KEY / CREW_OPENAI_BASE_URL",
        bool(env.get("OPENAI_API_KEY") or openai_base),
        openai_base,
    )
    add(
        "groq",
        "groq/" + env.get("CREW_GROQ_MODEL", "llama-3.3-70b-versatile"),
        "GROQ_API_KEY",
        bool(env.get("GROQ_API_KEY")),
    )
    add(
        "google",
        "gemini/" + env.get("CREW_GOOGLE_MODEL", "gemini-2.0-flash"),
        "GOOGLE_API_KEY",
        bool(env.get("GOOGLE_API_KEY")),
    )
    add(
        "cerebras",
        "cerebras/" + env.get("CREW_CEREBRAS_MODEL", "llama3.1-8b"),
        "CEREBRAS_API_KEY",
        bool(env.get("CEREBRAS_API_KEY")),
    )
    add(
        "mistral",
        "mistral/" + env.get("CREW_MISTRAL_MODEL", "mistral-small-latest"),
        "MISTRAL_API_KEY",
        bool(env.get("MISTRAL_API_KEY")),
    )
    ollama_model: str | None = None
    if env.get("CREW_ALLOW_OLLAMA", "1") != "0":
        ollama_model = _ollama_first_model(
            env.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        )
    add(
        "ollama",
        f"ollama/{ollama_model}" if ollama_model else "(no local ollama detected)",
        "autodetect",
        bool(ollama_model),
        connector="litellm",
        via="env" if ollama_model else "",
        configured=bool(ollama_model),
    )

    pin = env.get("CREW_PROVIDER", "").strip().lower()
    if pin in {"openai", "ov", "vault", "gemini"}:
        pin = {
            "openai": "openai-compatible",
            "ov": "openvault",
            "vault": "openvault",
            "gemini": "google",
        }[pin]

    out: list[Provider] = []
    if pin:
        matched = False
        for p in chain:
            if p.label.lower() == pin:
                matched = True
                out.append(replace(p, active=True) if p.configured else p)
            else:
                out.append(replace(p, active=False) if p.active else p)
        if not matched:
            return [replace(p, active=False) if p.active else p for p in chain]
        return out

    activated = False
    for p in chain:
        if p.configured and not activated:
            out.append(replace(p, active=True))
            activated = True
        else:
            out.append(p)
    return out


def active_provider(chain: list[Provider] | None = None) -> Provider | None:
    for p in chain if chain is not None else resolve_providers():
        if p.active:
            return p
    return None
