"""CREW-ROUTER: per-turn host pick, usage totals, fail-closed connectors."""

from __future__ import annotations

import pytest

from CortexOS.crew import connectors, llm, openvault
from CortexOS.crew.llm import LLMError, LLMResult, resolve_route
from tests.test_crew.conftest import wait_run_done


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for var in (
        "CREW_MODEL",
        "CREW_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "CREW_OPENAI_BASE_URL",
        "CURSOR_API_KEY",
        "XAI_API_KEY",
        "GROQ_API_KEY",
        "GOOGLE_API_KEY",
        "CEREBRAS_API_KEY",
        "MISTRAL_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "0")
    monkeypatch.setenv("CREW_OPENVAULT", "0")
    monkeypatch.setenv("CREW_LIVE_PROBES", "0")
    from CortexOS.crew.openvault import reset_vault_cache

    reset_vault_cache()
    return monkeypatch


def test_resolve_route_uses_operator_pick(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ANTHROPIC_API_KEY", "x")
    clean_env.setenv("DEEPSEEK_API_KEY", "y")
    route = resolve_route(provider="deepseek")
    assert route.label == "deepseek"
    assert route.model.startswith("deepseek/")
    assert route.connector == "litellm"


def test_resolve_route_refuses_unset_provider_without_fallback(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("DEEPSEEK_API_KEY", "y")
    with pytest.raises(LLMError, match="no silent fallback"):
        resolve_route(provider="anthropic")
    with pytest.raises(LLMError, match="unknown provider"):
        resolve_route(provider="not-a-host")


def test_openvault_pin_refuses_when_vault_is_down(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CREW_OPENVAULT", "0")
    clean_env.setenv("ANTHROPIC_API_KEY", "x")
    with pytest.raises(LLMError, match="OpenVault connector refused"):
        resolve_route(provider="openvault")
    with pytest.raises(LLMError, match="CREW_OPENVAULT=0"):
        openvault.require_live()


def test_connector_require_names_the_reason(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CREW_OPENVAULT", "0")
    rows = connectors.catalog()
    ov = next(r for r in rows if r["slug"] == "openvault")
    assert ov["connected"] is False
    assert ov["detail"]
    with pytest.raises(connectors.ConnectorError, match="no silent fallback"):
        connectors.require("openvault")
    with pytest.raises(connectors.ConnectorError, match="unknown connector"):
        connectors.require("not-a-plug")


def test_usage_ledger_is_visible() -> None:
    llm.reset_usage()
    llm.record_usage(
        LLMResult(text="ok", model="deepseek/deepseek-chat", prompt_tokens=11, completion_tokens=7, cost_usd=0.01),
        route="deepseek/deepseek-chat",
    )
    snap = llm.usage_snapshot()
    assert snap["llm_calls"] == 1
    assert snap["prompt_tokens"] == 11
    assert snap["completion_tokens"] == 7
    assert snap["cost_usd"] == 0.01
    assert snap["by_route"]["deepseek/deepseek-chat"]["llm_calls"] == 1
    view = llm.usage_view({"llm_calls": 4, "prompt_tokens": 20, "completion_tokens": 5, "cost_usd": 0.2})
    assert view["llm_calls"] == 4
    assert view["tokens"] == 25
    llm.reset_usage()


@pytest.mark.asyncio
async def test_per_turn_provider_is_stamped_and_usage_lands(rig, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    space = rig.store.create_space("HQ")
    rig.llm.manager.append(
        LLMResult(text="Via deepseek.", model="deepseek/deepseek-chat", prompt_tokens=3, completion_tokens=2)
    )
    await rig.runtime.on_user_message(space["id"], "hello", provider="deepseek")
    await wait_run_done(rig.runtime, space["id"])
    msgs = rig.store.list_messages(space["id"])
    assert msgs[-1]["content"] == "Via deepseek."
    assert msgs[-1]["meta"]["provider"] == "deepseek"
    assert msgs[-1]["meta"]["route"].startswith("deepseek/")
    assert msgs[-1]["meta"]["llm_calls"] == 1
    assert msgs[-1]["meta"]["prompt_tokens"] == 3
    totals = rig.store.usage_totals()
    assert totals["llm_calls"] == 1
    assert totals["tokens"] == 5


@pytest.mark.asyncio
async def test_per_turn_miss_is_a_visible_refuse(rig) -> None:
    space = rig.store.create_space("HQ")
    result = await rig.runtime.on_user_message(space["id"], "hello", provider="anthropic")
    assert "error" in result
    assert "no silent fallback" in result["error"]
    sysmsgs = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "system"]
    assert sysmsgs and "anthropic" in sysmsgs[0]["content"]


def _live_vault(monkeypatch: pytest.MonkeyPatch, rows: dict[str, dict]) -> None:
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    monkeypatch.setattr(
        openvault, "healthz", lambda timeout=1.5: {"ok": True, "url": "http://127.0.0.1:5000"}
    )
    monkeypatch.setattr(openvault, "require_live", lambda timeout=1.5: {"ok": True})
    monkeypatch.setattr(openvault, "vault_sources", lambda: rows)


def test_vault_armed_source_routes_via_freeroute_without_env_secret(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live_vault(
        monkeypatch,
        {
            "groq": {
                "id": "k-groq",
                "label": "GROQ_API_KEY",
                "provider": "groq",
                "env_key": "GROQ_API_KEY",
                "enabled": True,
                "crew_label": "groq",
            }
        },
    )
    from CortexOS.crew.config import resolve_providers

    groq = next(p for p in resolve_providers() if p.label == "groq")
    assert groq.configured is True
    assert groq.armed_via == "openvault"
    assert groq.connector == "openvault"
    assert groq.model.startswith("openvault/")
    route = resolve_route(provider="groq")
    assert route.label == "groq"
    assert route.connector == "openvault"
    assert route.model.startswith("openvault/")
    assert route.as_public()["connector"] == "openvault"
    snap = llm.chosen_public(provider="groq")
    assert snap["refused"] is None
    assert snap["chosen"]["label"] == "groq"


def test_vault_disarmed_source_refuses_without_fallback(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live_vault(
        monkeypatch,
        {
            "groq": {
                "id": "k-groq",
                "label": "GROQ_API_KEY",
                "provider": "groq",
                "env_key": "GROQ_API_KEY",
                "enabled": False,
                "crew_label": "groq",
            }
        },
    )
    with pytest.raises(LLMError, match="no silent fallback"):
        resolve_route(provider="groq")
    groq = next(r for r in connectors.catalog() if r["slug"] == "groq")
    assert groq["connected"] is False
    assert groq["armable"] is True
    assert "vault-disarmed" in groq["detail"]


def test_env_plus_vault_prefers_freeroute(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_env.setenv("GROQ_API_KEY", "x")
    _live_vault(
        monkeypatch,
        {
            "groq": {
                "id": "k-groq",
                "label": "GROQ_API_KEY",
                "provider": "groq",
                "env_key": "GROQ_API_KEY",
                "enabled": True,
                "crew_label": "groq",
            }
        },
    )
    route = resolve_route(provider="groq")
    assert route.connector == "openvault"
    assert route.armed_via == "both"
    groq = next(r for r in connectors.catalog() if r["slug"] == "groq")
    assert groq["armed"] is True
    assert groq["armed_via"] == "both"


def test_unarmed_model_string_refuses(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(LLMError, match="no silent fallback"):
        resolve_route(model="anthropic/claude-sonnet-5")
    with pytest.raises(LLMError, match="unarmed model"):
        resolve_route(model="not-a-host/mystery")


def test_catalog_marks_vault_armed_api(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live_vault(
        monkeypatch,
        {
            "openrouter": {
                "id": "k-or",
                "label": "OPENROUTER_API_KEY",
                "provider": "openrouter",
                "env_key": "OPENROUTER_API_KEY",
                "enabled": True,
                "crew_label": "openrouter",
            }
        },
    )
    row = next(r for r in connectors.catalog() if r["slug"] == "openrouter")
    assert row["connected"] is True
    assert row["armed"] is True
    assert row["armable"] is True
    assert row["armed_via"] == "openvault"
    connectors.require("openrouter")


def test_arm_grok_offloaded_refuses(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(connectors.ConnectorError, match="OFFLOADED"):
        connectors.arm("grok", True)
