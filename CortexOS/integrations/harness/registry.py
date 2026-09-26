"""The one provider table (PRD EPIC-HARNESS-ENT R1.1-R1.3). Stdlib only.

Every other provider list in Cortex is derived from :data:`ROWS`:
``direct_providers.PROVIDERS`` / ``KEY_ENVS``, crew ``keys.KEY_ENVS`` / ``KNOWN``,
and :func:`CortexOS.integrations.harness.secrets.secret_env_names`. A drift
test (``tests/harness/registry``) fails when one of them disagrees.

A model id resolves by its **exact** provider prefix (``xai/grok-4``), never by
a substring of the model name, and an unknown prefix raises
:class:`UnknownProvider`: nothing silently falls back to another host.

Prices are ``None`` until someone records one with ``as_of`` and ``source``;
an unpriced row is reported as unpriced, never priced at zero. ``region`` and
``retention`` default to ``"unknown"`` for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

AUTH_KINDS = frozenset({"bearer", "x-api-key", "azure-api-key", "bedrock-bearer", "sigv4", "gcp-oauth", "none"})
WIRES = frozenset({"openai_compat", "anthropic", "bedrock_converse", "vertex"})
CUSTODY_MODES = frozenset({"openvault", "env-direct"})
UNKNOWN = "unknown"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: Azure's base URL is the one host taken from env; only these suffixes pass.
AZURE_HOST_SUFFIXES = (".openai.azure.com", ".cognitiveservices.azure.com")
AZURE_BASE_ENV = "AZURE_OPENAI_ENDPOINT"


class UnknownProvider(LookupError):
    """A model id whose provider prefix is not a catalogued row id or alias."""


@dataclass(frozen=True)
class Price:
    """Micro-USD per million tokens. ``as_of`` and ``source`` are mandatory."""

    in_micro_usd_per_mtok: int
    out_micro_usd_per_mtok: int
    as_of: str
    source: str


@dataclass(frozen=True)
class ProviderRow:
    id: str
    key_envs: tuple[str, ...]
    auth: str
    base_url: str
    wire: str
    litellm_prefix: str
    default_model: str = ""
    aliases: tuple[str, ...] = ()
    region: str = UNKNOWN
    retention: str = UNKNOWN
    custody: tuple[str, ...] = ("openvault", "env-direct")
    price: Price | None = None
    #: Cortex's own env-direct transport can serve this row today.
    shipped: bool = False
    #: Loopback-only host (Ollama, vLLM, llama.cpp).
    local: bool = False

    @property
    def names(self) -> tuple[str, ...]:
        return (self.id, *self.aliases)


# Order: the four env-direct defaults first, in their exploration order; then
# the rest of the catalogue. Defaults for the first four are the models that
# answered a live chat on 2026-09-25 (docs/strategy/HANDOFF_SCALE_SESSIONS).
ROWS: tuple[ProviderRow, ...] = (
    ProviderRow(
        id="google",
        aliases=("gemini",),
        key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        auth="bearer",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        wire="openai_compat",
        litellm_prefix="gemini",
        default_model="gemini-3-flash-preview",
        shipped=True,
    ),
    ProviderRow(
        id="nvidia",
        aliases=("nvidia_nim",),
        key_envs=("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"),
        auth="bearer",
        base_url="https://integrate.api.nvidia.com/v1",
        wire="openai_compat",
        litellm_prefix="nvidia_nim",
        default_model="moonshotai/kimi-k3",
        shipped=True,
    ),
    ProviderRow(
        id="mistral",
        key_envs=("MISTRAL_API_KEY",),
        auth="bearer",
        base_url="https://api.mistral.ai/v1",
        wire="openai_compat",
        litellm_prefix="mistral",
        default_model="mistral-medium-latest",
        shipped=True,
    ),
    ProviderRow(
        id="cerebras",
        key_envs=("CEREBRAS_API_KEY",),
        auth="bearer",
        base_url="https://api.cerebras.ai/v1",
        wire="openai_compat",
        litellm_prefix="cerebras",
        default_model="gpt-oss-120b",
        shipped=True,
    ),
    ProviderRow(
        id="openai",
        key_envs=("OPENAI_API_KEY",),
        auth="bearer",
        base_url="https://api.openai.com/v1",
        wire="openai_compat",
        litellm_prefix="openai",
        shipped=True,
    ),
    ProviderRow(
        id="xai",
        key_envs=("XAI_API_KEY",),
        auth="bearer",
        base_url="https://api.x.ai/v1",
        wire="openai_compat",
        litellm_prefix="xai",
        shipped=True,
    ),
    ProviderRow(
        id="deepseek",
        key_envs=("DEEPSEEK_API_KEY",),
        auth="bearer",
        base_url="https://api.deepseek.com/v1",
        wire="openai_compat",
        litellm_prefix="deepseek",
        shipped=True,
    ),
    ProviderRow(
        id="groq",
        key_envs=("GROQ_API_KEY",),
        auth="bearer",
        base_url="https://api.groq.com/openai/v1",
        wire="openai_compat",
        litellm_prefix="groq",
        shipped=True,
    ),
    ProviderRow(
        id="openrouter",
        key_envs=("OPENROUTER_API_KEY",),
        auth="bearer",
        base_url="https://openrouter.ai/api/v1",
        wire="openai_compat",
        litellm_prefix="openrouter",
        shipped=True,
    ),
    ProviderRow(
        id="together",
        aliases=("together_ai",),
        key_envs=("TOGETHER_API_KEY", "TOGETHERAI_API_KEY"),
        auth="bearer",
        base_url="https://api.together.xyz/v1",
        wire="openai_compat",
        litellm_prefix="together_ai",
        shipped=True,
    ),
    ProviderRow(
        id="fireworks",
        aliases=("fireworks_ai",),
        key_envs=("FIREWORKS_API_KEY", "FIREWORKS_AI_API_KEY"),
        auth="bearer",
        base_url="https://api.fireworks.ai/inference/v1",
        wire="openai_compat",
        litellm_prefix="fireworks_ai",
        shipped=True,
    ),
    # Wired in HX-06 (PRD R10.2); [P] rows are not served until then.
    ProviderRow(
        id="cursor",
        key_envs=("CURSOR_API_KEY",),
        auth="bearer",
        base_url="https://api.cursor.com/v1",
        wire="openai_compat",
        litellm_prefix="cursor",
    ),
    ProviderRow(
        id="cohere",
        key_envs=("COHERE_API_KEY", "CO_API_KEY"),
        auth="bearer",
        base_url="https://api.cohere.ai/compatibility/v1",
        wire="openai_compat",
        litellm_prefix="cohere",
    ),
    ProviderRow(
        id="anthropic",
        key_envs=("ANTHROPIC_API_KEY",),
        auth="x-api-key",
        base_url="https://api.anthropic.com/v1",
        wire="anthropic",
        litellm_prefix="anthropic",
    ),
    ProviderRow(
        id="azure",
        aliases=("azure_openai",),
        key_envs=("AZURE_OPENAI_API_KEY", "AZURE_API_KEY"),
        auth="azure-api-key",
        base_url="",  # from AZURE_OPENAI_ENDPOINT, https + Azure host only
        wire="openai_compat",
        litellm_prefix="azure",
    ),
    ProviderRow(
        id="bedrock",
        key_envs=("AWS_BEARER_TOKEN_BEDROCK",),
        auth="bedrock-bearer",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        wire="bedrock_converse",
        litellm_prefix="bedrock",
    ),
    ProviderRow(
        id="vertex",
        aliases=("vertex_ai",),
        key_envs=(),
        auth="gcp-oauth",
        base_url="https://aiplatform.googleapis.com/v1",
        wire="vertex",
        litellm_prefix="vertex_ai",
    ),
    ProviderRow(
        id="ollama",
        aliases=("ollama_chat",),
        key_envs=(),
        auth="none",
        base_url="http://127.0.0.1:11434/v1",
        wire="openai_compat",
        litellm_prefix="ollama",
        custody=("env-direct",),
        local=True,
    ),
    ProviderRow(
        id="vllm",
        aliases=("hosted_vllm",),
        key_envs=(),
        auth="none",
        base_url="http://127.0.0.1:8000/v1",
        wire="openai_compat",
        litellm_prefix="hosted_vllm",
        custody=("env-direct",),
        local=True,
    ),
    ProviderRow(
        id="llamacpp",
        aliases=("llama_cpp",),
        key_envs=(),
        auth="none",
        base_url="http://127.0.0.1:8080/v1",
        wire="openai_compat",
        litellm_prefix="llamacpp",
        custody=("env-direct",),
        local=True,
    ),
)

#: Env-direct serves these rows with no opt-in, in this order (PRD R1.5).
DIRECT_DEFAULTS: tuple[str, ...] = ("google", "nvidia", "mistral", "cerebras")

_BY_NAME: dict[str, ProviderRow] = {name: r for r in ROWS for name in r.names}


def row(provider_id: str) -> ProviderRow:
    """The row whose id or alias is exactly ``provider_id`` (case-insensitive)."""
    found = _BY_NAME.get((provider_id or "").strip().lower())
    if found is None:
        raise UnknownProvider(f"unknown provider {provider_id!r}")
    return found


def resolve(model_id: str) -> tuple[ProviderRow, str]:
    """``<provider>/<model>`` -> (row, model). Exact prefix only, never a substring.

    ``openrouter/x-ai/grok-4`` is OpenRouter serving ``x-ai/grok-4``; the word
    ``grok`` in a model name never routes a call to xAI or Cursor.
    """
    prefix, sep, model = (model_id or "").strip().partition("/")
    if not sep or not prefix or not model:
        raise UnknownProvider(f"model id must be <provider>/<model>; got {model_id!r}")
    return row(prefix), model


def key_envs(provider_id: str) -> tuple[str, ...]:
    return row(provider_id).key_envs


def all_key_envs() -> tuple[str, ...]:
    """Every provider key env name, primary first per row, no duplicates."""
    return tuple(dict.fromkeys(n for r in ROWS for n in r.key_envs))


def default_model(provider_id: str) -> str:
    return row(provider_id).default_model


def is_loopback(host: str) -> bool:
    h = (host or "").strip().lower().strip("[]")
    return h in LOOPBACK_HOSTS or h.startswith("127.")


def safe_base_url(url: str) -> bool:
    """https, or http to a loopback host. Nothing else carries a credential."""
    parts = urlsplit((url or "").strip())
    if not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and is_loopback(parts.hostname)


def azure_base_url(value: str) -> str:
    """The operator's Azure endpoint if it is https on an Azure OpenAI host, else ''."""
    parts = urlsplit((value or "").strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host.endswith(AZURE_HOST_SUFFIXES):
        return ""
    return f"https://{parts.netloc}".rstrip("/")


__all__ = [
    "AUTH_KINDS",
    "AZURE_BASE_ENV",
    "CUSTODY_MODES",
    "DIRECT_DEFAULTS",
    "Price",
    "ProviderRow",
    "ROWS",
    "UNKNOWN",
    "UnknownProvider",
    "WIRES",
    "all_key_envs",
    "azure_base_url",
    "default_model",
    "is_loopback",
    "key_envs",
    "resolve",
    "row",
    "safe_base_url",
]
