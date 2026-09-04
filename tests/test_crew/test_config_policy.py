from __future__ import annotations

import pytest

from CortexOS.crew import config, policy


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for var in (
        "CREW_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "CREW_OPENAI_BASE_URL",
        "CURSOR_API_KEY",
        "XAI_API_KEY",
        "GMAIL_IMAP_USER",
        "GMAIL_APP_PASSWORD",
        "GROQ_API_KEY",
        "GOOGLE_API_KEY",
        "CEREBRAS_API_KEY",
        "MISTRAL_API_KEY",
        "CREW_PROVIDER",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "0")
    monkeypatch.setenv("CREW_OPENVAULT", "0")
    monkeypatch.setenv("CREW_LIVE_PROBES", "0")
    return monkeypatch


def test_no_keys_means_no_active_provider_and_says_so(clean_env: pytest.MonkeyPatch) -> None:
    chain = config.resolve_providers()
    assert config.active_provider(chain) is None
    # the chain still names every option so the operator knows what to set
    assert {p.label for p in chain} == {
        "explicit",
        "openvault",
        "anthropic",
        "cursor",
        "openrouter",
        "xai",
        "deepseek",
        "openai-compatible",
        "groq",
        "google",
        "cerebras",
        "mistral",
        "ollama",
    }


def test_first_configured_key_wins_and_is_stamped(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DEEPSEEK_API_KEY", "x")
    clean_env.setenv("OPENAI_API_KEY", "y")
    active = config.active_provider()
    assert active is not None
    assert active.label == "deepseek"
    assert active.model == "deepseek/deepseek-chat"

    clean_env.setenv("ANTHROPIC_API_KEY", "z")
    active = config.active_provider()
    assert active is not None and active.label == "anthropic"
    assert active.model == "anthropic/claude-sonnet-5"

    clean_env.setenv("CREW_MODEL", "openrouter/some/cheap-model")
    active = config.active_provider()
    assert active is not None and active.label == "explicit"
    assert active.model == "openrouter/some/cheap-model"


def test_policy_master_switch_and_arming_fail_closed() -> None:
    # master off: even read-only capture is refused, with the fix named
    decision, reason = policy.decide(
        "Screenshot", server="windows-mcp", armed=True, master_on=False
    )
    assert decision == policy.DENY and "CORTEX_COMPUTER_CONTROL" in reason

    decision, reason = policy.decide(
        "Screenshot", server="windows-mcp", armed=False, master_on=True
    )
    assert decision == policy.DENY and "not armed" in reason

    decision, _ = policy.decide("Screenshot", server="windows-mcp", armed=True, master_on=True)
    assert decision == policy.ALLOW

    # mutating tools always take the confirm path, unknown tools included
    decision, _ = policy.decide("Type", server="windows-mcp", armed=True, master_on=True)
    assert decision == policy.CONFIRM
    decision, _ = policy.decide("BrandNewTool", server="windows-mcp", armed=True, master_on=True)
    assert decision == policy.CONFIRM

    # crew-internal tools are allowed; unknown internal names are not
    assert policy.decide("cortex_ask", server=None, armed=False, master_on=False)[0] == policy.ALLOW
    assert policy.decide("netie_board", server=None, armed=False, master_on=False)[0] == policy.ALLOW
    assert policy.decide("desk_status", server=None, armed=False, master_on=False)[0] == policy.ALLOW
    assert policy.decide("estate_status", server=None, armed=False, master_on=False)[0] == policy.ALLOW
    assert policy.decide("ship_gate", server=None, armed=False, master_on=False)[0] == policy.ALLOW
    assert policy.decide("show_issue", server=None, armed=False, master_on=False)[0] == policy.ALLOW
    assert policy.decide("rm_rf", server=None, armed=False, master_on=False)[0] == policy.DENY
    denied, reason = policy.decide(
        "click",
        server="uacc",
        armed=True,
        master_on=True,
        denied=frozenset({"click"}),
    )
    assert denied == policy.DENY and "grant" in reason
    allowed, _ = policy.decide(
        "click",
        server="uacc",
        armed=True,
        master_on=True,
        allowed=frozenset({"screenshot"}),
    )
    assert allowed == policy.DENY


def test_login_wall_is_takeover_not_silent_type() -> None:
    assert policy.needs_takeover("Type", {"password": "x"}) is True
    assert policy.needs_takeover("click", {"selector": "#login"}) is True
    assert policy.needs_takeover("fill", {"otp": "123456"}) is True
    assert policy.needs_takeover("click", {"x": 10, "y": 20}) is False


def test_cursor_key_defaults_to_grok_46_not_fast(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CURSOR_API_KEY", "x")
    active = config.active_provider()
    assert active is not None
    assert active.label == "cursor"
    assert active.model == "openai/grok-4.6"
    assert "fast" not in active.model


def test_provider_pin_does_not_fall_through(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DEEPSEEK_API_KEY", "x")
    clean_env.setenv("OPENAI_API_KEY", "y")
    clean_env.setenv("CREW_PROVIDER", "openai")
    active = config.active_provider()
    assert active is not None
    assert active.label == "openai-compatible"

    clean_env.setenv("CREW_PROVIDER", "anthropic")
    chain = config.resolve_providers()
    assert config.active_provider(chain) is None
    assert all(not p.active for p in chain)

