"""HX-01: the one provider registry, exact-prefix resolution, and drift (PRD R1.1-R1.3).

Stubbed only: no key, no network.
"""

from __future__ import annotations

import pytest

from CortexOS.integrations.harness import registry
from CortexOS.integrations.harness import secrets as harness_secrets

# -- R1.2: exact prefix, never a substring ---------------------------------------


@pytest.mark.parametrize(
    ("model_id", "provider", "host", "keys", "bare"),
    [
        ("xai/grok-4", "xai", "api.x.ai", ("XAI_API_KEY",), "grok-4"),
        ("openrouter/x-ai/grok-4", "openrouter", "openrouter.ai", ("OPENROUTER_API_KEY",), "x-ai/grok-4"),
        ("openai/grok-4.6", "openai", "api.openai.com", ("OPENAI_API_KEY",), "grok-4.6"),
        ("cursor/grok-4.6", "cursor", "api.cursor.com", ("CURSOR_API_KEY",), "grok-4.6"),
        ("nvidia_nim/moonshotai/kimi-k3", "nvidia", "integrate.api.nvidia.com", None, "moonshotai/kimi-k3"),
        ("gemini/gemini-3-flash-preview", "google", "generativelanguage.googleapis.com", None, "gemini-3-flash-preview"),
    ],
)
def test_model_id_resolves_by_exact_prefix(model_id, provider, host, keys, bare) -> None:
    row, model = registry.resolve(model_id)
    assert row.id == provider
    assert model == bare
    assert row.base_url.startswith(f"https://{host}/")
    if keys is not None:
        assert row.key_envs == keys


def test_cursor_row_sends_only_the_cursor_key() -> None:
    row, _ = registry.resolve("cursor/grok-4.6")
    assert row.key_envs == ("CURSOR_API_KEY",)
    for other in ("XAI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        assert other not in row.key_envs


@pytest.mark.parametrize("model_id", ["grok-4.6", "grok/grok-4", "x-ai/grok-4", "self_hosted/x", "", "openai/", "/gpt"])
def test_unknown_or_bare_prefix_raises_never_falls_back(model_id) -> None:
    with pytest.raises(registry.UnknownProvider):
        registry.resolve(model_id)


# -- R1.1: row shape ------------------------------------------------------------


def test_every_row_is_well_formed() -> None:
    ids = [r.id for r in registry.ROWS]
    names = [n for r in registry.ROWS for n in r.names]
    assert len(names) == len(set(names)), "an id or alias names two rows"
    assert len(ids) >= 20
    for r in registry.ROWS:
        assert r.auth in registry.AUTH_KINDS, r.id
        assert r.wire in registry.WIRES, r.id
        assert set(r.custody) <= registry.CUSTODY_MODES, r.id
        assert r.region and r.retention, r.id
        if r.price is not None:
            assert r.price.as_of and r.price.source, r.id
        if r.local:
            assert r.base_url.startswith("http://127.0.0.1:"), r.id
            assert r.custody == ("env-direct",), r.id
        elif r.id == "azure":
            assert r.base_url == ""
        else:
            assert r.base_url.startswith("https://"), r.id
        for env in r.key_envs:
            assert env.isupper() and " " not in env, env


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("https://acme.openai.azure.com", True),
        ("https://acme.cognitiveservices.azure.com/", True),
        ("http://acme.openai.azure.com", False),
        ("https://evil.example.com", False),
        ("https://openai.azure.com.evil.example", False),
        ("", False),
    ],
)
def test_azure_base_url_is_https_on_an_azure_host_only(value, ok) -> None:
    assert bool(registry.azure_base_url(value)) is ok


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://vault.example.com", True),
        ("http://127.0.0.1:8000", True),
        ("http://localhost:8000", True),
        ("http://[::1]:8000", True),
        ("http://vault.example.com", False),
        ("http://10.0.0.5:8000", False),
        ("ftp://127.0.0.1", False),
        ("", False),
    ],
)
def test_safe_base_url_is_https_or_loopback(url, ok) -> None:
    assert registry.safe_base_url(url) is ok


# -- R1.3: every derived registry agrees with the one table ---------------------


def test_direct_providers_are_derived_from_the_registry() -> None:
    from CortexOS.integrations import direct_providers

    assert tuple(p.label for p in direct_providers.PROVIDERS) == registry.DIRECT_DEFAULTS
    for p in direct_providers.CATALOGUE:
        row = registry.row(p.label)
        assert p.key_envs == row.key_envs
        assert p.base == row.base_url
        assert p.models == ((row.default_model,) if row.default_model else ())
    assert direct_providers.KEY_ENVS == harness_secrets.secret_env_names()


def test_crew_key_tables_are_derived_from_the_registry() -> None:
    from CortexOS.crew import keys

    for label, names in keys.KEY_ENVS.items():
        assert names == registry.key_envs(label)
    for prefix, label in keys.KEY_PREFIXES.items():
        assert registry.resolve(f"{prefix}/m")[0].id == label
    provider_keys = {n for n in keys.KNOWN if n.endswith("_API_KEY")}
    assert provider_keys <= set(registry.all_key_envs())


def test_other_key_env_lists_name_only_registry_envs() -> None:
    from CortexOS.crew import connectors
    from CortexOS.execution import workflow_openvault

    known = set(registry.all_key_envs())
    assert set(workflow_openvault._KEY_ENV.values()) <= known
    crew_rows = {r["env"] for r in connectors._API_ROWS if r.get("env", "").endswith("_API_KEY")}
    assert crew_rows <= known
