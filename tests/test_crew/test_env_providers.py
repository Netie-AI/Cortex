"""CREW-ENV-KEYS: GEMINI / NVIDIA / Cerebras env keys arm the Crew chain.

The operator's env holds GEMINI_API_KEY (not GOOGLE_API_KEY) and NVIDIA_API_KEY.
Before this, google read only GOOGLE_API_KEY, there was no nvidia row, and the
google / cerebras defaults were model ids the hosts no longer serve. Every
assertion here is on what the operator sees: the chain rows, the chosen route,
the transcript message, and the key litellm is actually handed. No network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from CortexOS.crew import config, connectors, keys, llm, openvault
from CortexOS.crew.llm import LLMError, LLMResult, resolve_route
from tests.test_crew.conftest import wait_run_done

FAKE_GEMINI = "gm-test-not-real-gemini"
FAKE_GOOGLE = "gm-test-not-real-google"
FAKE_NVIDIA = "nvapi-test-not-real"
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"


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
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "NVIDIA_API_KEY",
        "NVIDIA_NIM_API_KEY",
        "CEREBRAS_API_KEY",
        "MISTRAL_API_KEY",
        "CREW_GOOGLE_MODEL",
        "CREW_NVIDIA_MODEL",
        "CREW_NVIDIA_BASE_URL",
        "CREW_CEREBRAS_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "0")
    monkeypatch.setenv("CREW_OPENVAULT", "0")
    monkeypatch.setenv("CREW_LIVE_PROBES", "0")
    openvault.reset_vault_cache()
    return monkeypatch


def _row(label: str) -> config.Provider:
    return next(p for p in config.resolve_providers() if p.label == label)


def _no_secret(blob: object) -> None:
    text = str(blob)
    for secret in (FAKE_GEMINI, FAKE_GOOGLE, FAKE_NVIDIA):
        assert secret not in text


# -- google reads GEMINI_API_KEY ---------------------------------------------


def test_gemini_key_alone_configures_google(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GEMINI_API_KEY", FAKE_GEMINI)

    google = _row("google")
    assert google.configured is True
    assert google.source == "GEMINI_API_KEY"
    assert google.model == "gemini/gemini-3-flash-preview"
    assert google.armed_via == "env"
    assert google.connector == "litellm"

    active = config.active_provider()
    assert active is not None and active.label == "google"

    # The connector gate resolve_route runs must agree, or the pick refuses.
    route = resolve_route(provider="google")
    assert route.label == "google"
    assert route.model == "gemini/gemini-3-flash-preview"
    assert route.connector == "litellm"
    assert resolve_route().label == "google"
    snap = llm.chosen_public()
    assert snap["refused"] is None
    assert snap["chosen"]["label"] == "google"

    row = next(r for r in connectors.catalog() if r["slug"] == "google")
    assert row["connected"] is True
    assert row["detail"] == "GEMINI_API_KEY configured"
    _no_secret([google.public(), route.as_public(), snap, row])


def test_google_api_key_still_works_and_gemini_name_wins(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("GOOGLE_API_KEY", FAKE_GOOGLE)
    assert _row("google").source == "GOOGLE_API_KEY"
    assert llm._api_key("gemini/gemini-3-flash-preview") == FAKE_GOOGLE

    # Both set: the stamped name and the spent key are the same one.
    clean_env.setenv("GEMINI_API_KEY", FAKE_GEMINI)
    assert _row("google").source == "GEMINI_API_KEY"
    assert llm._api_key("gemini/gemini-3-flash-preview") == FAKE_GEMINI


def test_unset_google_names_both_env_vars(clean_env: pytest.MonkeyPatch) -> None:
    google = _row("google")
    assert google.configured is False
    assert google.source == "GEMINI_API_KEY / GOOGLE_API_KEY"
    with pytest.raises(LLMError, match="no silent fallback") as err:
        resolve_route(provider="gemini")
    assert "GEMINI_API_KEY" in str(err.value)


# -- nvidia ------------------------------------------------------------------


def test_nvidia_key_configures_nvidia_with_nim_model_and_base(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("NVIDIA_API_KEY", FAKE_NVIDIA)

    nvidia = _row("nvidia")
    assert nvidia.configured is True
    assert nvidia.source == "NVIDIA_API_KEY"
    assert nvidia.model == "nvidia_nim/moonshotai/kimi-k3"
    assert nvidia.api_base == NVIDIA_BASE
    assert nvidia.connector == "litellm"

    route = resolve_route()
    assert route.label == "nvidia"
    assert route.model == "nvidia_nim/moonshotai/kimi-k3"
    assert route.api_base == NVIDIA_BASE
    assert route.connector == "litellm"
    assert resolve_route(provider="nim").label == "nvidia"
    # A litellm model string names the host on its own.
    assert resolve_route(model="nvidia_nim/meta/llama-4-maverick").label == "nvidia"

    row = next(r for r in connectors.catalog() if r["slug"] == "nvidia")
    assert row["connected"] is True
    assert row["armable"] is True
    assert row["detail"] == "NVIDIA_API_KEY configured"
    _no_secret([nvidia.public(), route.as_public(), row])


def test_nvidia_model_and_base_are_overridable(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("NVIDIA_API_KEY", FAKE_NVIDIA)
    clean_env.setenv("CREW_NVIDIA_MODEL", "meta/llama-4-maverick")
    clean_env.setenv("CREW_NVIDIA_BASE_URL", "http://nim.local/v1")
    nvidia = _row("nvidia")
    assert nvidia.model == "nvidia_nim/meta/llama-4-maverick"
    assert nvidia.api_base == "http://nim.local/v1"


def test_unset_nvidia_refuses_by_name(clean_env: pytest.MonkeyPatch) -> None:
    nvidia = _row("nvidia")
    assert nvidia.configured is False
    with pytest.raises(LLMError, match="no silent fallback") as err:
        resolve_route(provider="nvidia")
    assert "NVIDIA_API_KEY" in str(err.value)
    row = next(r for r in connectors.catalog() if r["slug"] == "nvidia")
    assert row["connected"] is False
    assert row["detail"] == "NVIDIA_API_KEY / NVIDIA_NIM_API_KEY unset"


# -- chain order and the strict pin ------------------------------------------


def test_chain_order_picks_google_and_nvidia_pin_is_strict(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("GEMINI_API_KEY", FAKE_GEMINI)
    clean_env.setenv("NVIDIA_API_KEY", FAKE_NVIDIA)
    clean_env.setenv("CEREBRAS_API_KEY", "csk-test-not-real")
    clean_env.setenv("MISTRAL_API_KEY", "mistral-test-not-real")

    chain = config.resolve_providers()
    labels = [p.label for p in chain]
    assert labels.index("google") < labels.index("nvidia") < labels.index("cerebras")
    assert [p.label for p in chain if p.active] == ["google"]
    assert {p.label for p in chain if p.configured} == {"google", "nvidia", "cerebras", "mistral"}

    clean_env.setenv("CREW_PROVIDER", "nvidia")
    chain = config.resolve_providers()
    assert [p.label for p in chain if p.active] == ["nvidia"]
    route = resolve_route()
    assert route.label == "nvidia"
    assert route.api_base == NVIDIA_BASE

    # Pinned to nvidia with no NVIDIA key: nothing is active; google does not
    # quietly take over.
    clean_env.delenv("NVIDIA_API_KEY")
    chain = config.resolve_providers()
    assert config.active_provider(chain) is None
    assert _row("google").configured is True
    with pytest.raises(LLMError, match="no silent fallback"):
        resolve_route()


# -- defaults ----------------------------------------------------------------


def test_default_models_are_live_ids(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GEMINI_API_KEY", FAKE_GEMINI)
    clean_env.setenv("NVIDIA_API_KEY", FAKE_NVIDIA)
    clean_env.setenv("CEREBRAS_API_KEY", "csk-test-not-real")
    assert _row("google").model == "gemini/gemini-3-flash-preview"
    assert _row("nvidia").model == "nvidia_nim/moonshotai/kimi-k3"
    assert _row("cerebras").model == "cerebras/gpt-oss-120b"
    for dead in ("gemini-2.0-flash", "llama3.1-8b"):
        assert all(dead not in p.model for p in config.resolve_providers())

    clean_env.setenv("CREW_GOOGLE_MODEL", "gemini-3-pro-preview")
    clean_env.setenv("CREW_CEREBRAS_MODEL", "qwen-3.8-27b")
    assert _row("google").model == "gemini/gemini-3-pro-preview"
    assert _row("cerebras").model == "cerebras/qwen-3.8-27b"


# -- the key litellm is handed -----------------------------------------------


class _FakeLitellm:
    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        message = SimpleNamespace(content=self.text, tool_calls=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2),
        )

    def completion_cost(self, completion_response: Any) -> float:
        return 0.0


@pytest.mark.asyncio
async def test_chat_hands_litellm_the_stamped_key(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_env.setenv("NVIDIA_API_KEY", FAKE_NVIDIA)
    clean_env.setenv("GEMINI_API_KEY", FAKE_GEMINI)
    clean_env.setenv("GOOGLE_API_KEY", FAKE_GOOGLE)
    clean_env.setenv("DEEPSEEK_API_KEY", "ds-test-not-real")
    fake = _FakeLitellm("pong from the host")
    monkeypatch.setattr(llm, "_litellm", lambda: fake)

    route = resolve_route(provider="nvidia")
    out = await llm.chat(
        route.model, [{"role": "user", "content": "ping"}], api_base=route.api_base
    )
    assert out.text == "pong from the host"
    assert out.model == "nvidia_nim/moonshotai/kimi-k3"
    sent = fake.kwargs[-1]
    assert sent["model"] == "nvidia_nim/moonshotai/kimi-k3"
    assert sent["api_base"] == NVIDIA_BASE
    # litellm reads NVIDIA_NIM_API_KEY only; without this the call is keyless.
    assert sent["api_key"] == FAKE_NVIDIA

    out = await llm.chat("gemini/gemini-3-flash-preview", [{"role": "user", "content": "ping"}])
    assert out.text == "pong from the host"
    # litellm alone would spend GOOGLE_API_KEY; the chain stamped GEMINI_API_KEY.
    assert fake.kwargs[-1]["api_key"] == FAKE_GEMINI

    # Hosts litellm already reads correctly keep litellm's own lookup.
    await llm.chat("deepseek/deepseek-chat", [{"role": "user", "content": "ping"}])
    assert "api_key" not in fake.kwargs[-1]


@pytest.mark.asyncio
async def test_chat_error_never_carries_the_key(
    clean_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_env.setenv("NVIDIA_API_KEY", FAKE_NVIDIA)

    class _Boom:
        async def acompletion(self, **kwargs: Any) -> Any:
            raise RuntimeError("upstream said 402 payment required")

    monkeypatch.setattr(llm, "_litellm", lambda: _Boom())
    with pytest.raises(LLMError) as err:
        await llm.chat("nvidia_nim/moonshotai/kimi-k3", [{"role": "user", "content": "x"}])
    assert "402 payment required" in str(err.value)
    _no_secret(err.value)


# -- the operator's transcript -----------------------------------------------


@pytest.mark.asyncio
async def test_nvidia_turn_lands_in_transcript_stamped_nvidia(
    rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CREW_MODEL", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", FAKE_NVIDIA)
    seen: list[dict[str, Any]] = []
    fake = rig.llm

    async def _spy(model: str, messages: list[dict[str, Any]], **kw: Any) -> LLMResult:
        seen.append({"model": model, "api_base": kw.get("api_base")})
        return await fake(model, messages, **kw)

    rig.runtime._llm = _spy
    fake.manager.append(
        LLMResult(
            text="Kimi here.",
            model="nvidia_nim/moonshotai/kimi-k3",
            prompt_tokens=3,
            completion_tokens=2,
        )
    )
    space = rig.store.create_space("HQ")
    await rig.runtime.on_user_message(space["id"], "hello")
    await wait_run_done(rig.runtime, space["id"])

    msgs = rig.store.list_messages(space["id"])
    assert msgs[-1]["content"] == "Kimi here."
    assert msgs[-1]["meta"]["provider"] == "nvidia"
    assert msgs[-1]["meta"]["route"] == "nvidia_nim/moonshotai/kimi-k3"
    assert seen and seen[0]["model"] == "nvidia_nim/moonshotai/kimi-k3"
    assert seen[0]["api_base"] == NVIDIA_BASE
    _no_secret(msgs)


# -- keys form and label maps ------------------------------------------------


def test_keys_form_and_label_maps_know_the_new_names() -> None:
    for name in ("GEMINI_API_KEY", "NVIDIA_API_KEY", "CREW_NVIDIA_MODEL", "CREW_NVIDIA_BASE_URL"):
        assert name in keys.KNOWN
    fields = {f["key"] for f in keys.public_fields()}
    assert {"GEMINI_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY"} <= fields
    assert llm._label_for_model("nvidia_nim/moonshotai/kimi-k3") == "nvidia"
    assert llm._label_for_model("gemini/gemini-3-flash-preview") == "google"
    assert llm._norm_provider("nvidia_nim") == "nvidia"
