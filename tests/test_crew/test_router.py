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


def test_connector_require_names_the_reason() -> None:
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
