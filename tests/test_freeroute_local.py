"""Cortex #272 LOCAL-1: local hop arming, served_* stamps, LOCAL_ONLY fail-closed.

Stubbed OpenVault transport only. No keys, no network, no skip markers.
OpenVault#71 confirms served_provider / served_model / served_local /
local_only / local_reason and the 503 / 403 refusal shapes.
"""

from __future__ import annotations

import json

import pytest

from CortexOS.crew import insights
from CortexOS.integrations import freeroute as fr
from CortexOS.integrations import freeroute_ov_local as ov_local

LOCAL_PROVIDER = ov_local.LOCAL_PROVIDER_ID
LOCAL_MODEL = "qwen2.5-1.5b"
CLOUD_ANSWER = "CLOUD-ANSWER-MUST-BE-DROPPED-272"


def _install_local_hop(fake, *, pooled: int = 0, local_reason: str = "") -> None:
    fake.pooled = pooled
    fake.catalogue = {LOCAL_PROVIDER: [LOCAL_MODEL]}
    fake.local_providers = {LOCAL_PROVIDER}
    fake.local_reason = local_reason
    fake.hops = [fake.hop(LOCAL_PROVIDER, 1, served_local=True)]


def _sql_ok(text: str) -> bool:
    return "select" in text.lower() and "from" in text.lower()


# -- adapter: OV#71 names; never infer from a model id --------------------------


def test_adapter_missing_fields_are_null_false_plus_named_reason() -> None:
    provider, model, local, reason = ov_local.served_from_response(
        {"model": "ollama/qwen2.5", "choices": [{"message": {"content": "hi"}}]}
    )
    assert provider is None
    assert model is None
    assert local is False
    assert ov_local.MISSING_PROVIDER in reason
    assert ov_local.MISSING_MODEL in reason
    assert ov_local.MISSING_LOCAL in reason


def test_adapter_never_infers_local_from_provider_or_model_name() -> None:
    assert ov_local.hop_reported_local({"provider": "ollama", "model": "qwen2.5"}) is False
    assert ov_local.hop_reported_local({"provider": LOCAL_PROVIDER, "tier": "local"}) is False
    assert ov_local.hop_reported_local({"served_local": True}) is True
    assert ov_local.hop_reported_local({"served_local": "true"}) is False
    assert ov_local.hop_reported_local({"served_local": False}) is False


def test_adapter_local_arming_reason_is_pass_through() -> None:
    assert ov_local.local_arming_reason({}) == ""
    assert ov_local.local_arming_reason({"local_reason": ov_local.LOCAL_REASON_UNREACHABLE}) == (
        ov_local.LOCAL_REASON_UNREACHABLE
    )
    assert ov_local.local_arming_reason(
        {"local_reason": ov_local.LOCAL_REASON_MODEL_NOT_LOADED}
    ) == ov_local.LOCAL_REASON_MODEL_NOT_LOADED
    assert ov_local.local_arming_reason(
        {"local_reason": ov_local.LOCAL_REASON_NOT_LOOPBACK}
    ) == ov_local.LOCAL_REASON_NOT_LOOPBACK


def test_adapter_reads_only_explicit_ov71_stamp_names() -> None:
    provider, model, local, reason = ov_local.served_from_response(
        {
            "served_provider": LOCAL_PROVIDER,
            "served_model": LOCAL_MODEL,
            "served_local": True,
            "model": "groq-should-not-win",
        }
    )
    assert provider == LOCAL_PROVIDER
    assert model == LOCAL_MODEL
    assert local is True
    assert reason == ""


# -- arming: OpenVault-reported local spendable hop ----------------------------


def test_local_spendable_hop_arms_without_pooled_cloud_keys(armed_openvault) -> None:
    """Gate 5829081052: arming accepts an OpenVault-reported local hop."""
    _install_local_hop(armed_openvault, pooled=0)
    arm = fr.arming(fresh=True)
    assert arm.armed is True
    assert arm.local_spendable_hops == 1
    assert arm.pooled_keys == 0
    assert arm.public()["local_only"] is False
    assert "armed:" in arm.reason


def test_ollama_model_name_without_local_flag_does_not_arm(armed_openvault) -> None:
    armed_openvault.pooled = 0
    armed_openvault.catalogue = {"ollama": ["qwen2.5", "llama3.2"]}
    armed_openvault.local_providers = set()
    armed_openvault.hops = [armed_openvault.hop("ollama", 1)]
    arm = fr.arming(fresh=True)
    assert arm.armed is False
    assert "pools no keys" in arm.reason


@pytest.mark.parametrize(
    "named",
    [
        ov_local.LOCAL_REASON_UNREACHABLE,
        ov_local.LOCAL_REASON_MODEL_NOT_LOADED,
        ov_local.LOCAL_REASON_NOT_LOOPBACK,
    ],
)
def test_arming_passes_through_ov71_local_reason(armed_openvault, named) -> None:
    _install_local_hop(armed_openvault, pooled=0, local_reason=named)
    arm = fr.arming(fresh=True)
    assert arm.armed is True
    assert arm.local_reason == named
    assert arm.public()["local_reason"] == named


# -- unchanged named refusals still win when a local hop is also present -------


@pytest.mark.parametrize(
    ("setup", "needle"),
    [
        (lambda f: setattr(f, "sealed", True), "vault is sealed"),
        (lambda f: setattr(f, "sealed", None), "did not report seal state"),
        (lambda f: setattr(f, "sealed", "false"), "did not report seal state"),
        (lambda f: setattr(f, "status_code", 500), "answered HTTP 500"),
        (
            lambda f: (
                setattr(f, "hops", [f.hop("cortex", 10, served_local=True)]),
                setattr(f, "catalogue", {}),
                setattr(f, "local_providers", set()),
            ),
            "no spendable FreeRoute hop",
        ),
    ],
)
def test_unchanged_vault_refusals_still_hold_with_local_hop_scripted(
    armed_openvault, setup, needle
) -> None:
    _install_local_hop(armed_openvault, pooled=3)
    setup(armed_openvault)
    arm = fr.arming(fresh=True)
    assert arm.armed is False
    assert needle in arm.reason
    out = fr.complete("t", [{"role": "user", "content": "hi"}])
    assert out.ok is False and needle in out.reason
    assert armed_openvault.chat_calls == []


def test_unchanged_pools_no_keys_when_no_local_flag(armed_openvault) -> None:
    armed_openvault.pooled = 0
    arm = fr.arming(fresh=True)
    assert arm.armed is False
    assert "pools no keys" in arm.reason
    assert arm.public()["local_only"] is False


def test_unchanged_env_keys_and_process_ollama_never_arm(armed_openvault, monkeypatch) -> None:
    for name in ("DEEPSEEK_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "sk-" + "x" * 30)
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "1")
    armed_openvault.pooled = 0
    arm = fr.arming(fresh=True)
    assert arm.armed is False
    assert "pools no keys" in arm.reason
    assert armed_openvault.non_openvault_calls == []
    assert [c["path"] for c in armed_openvault.calls] == ["/api/freeroute/status"]


def test_unchanged_ov_key_and_https_rules_ignore_local_hop(armed_openvault, monkeypatch) -> None:
    _install_local_hop(armed_openvault, pooled=3)
    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", "sk-abcdefghijklmnop")
    assert "not an OpenVault ov_ key" in fr.arming(fresh=True).reason
    assert armed_openvault.calls == []

    fr.reset()
    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", "ov_" + "Zq8vN2kR7tY4wP1mX6cB9dF3")
    monkeypatch.setenv("OPENVAULT_BASE_URL", "http://vault.example.com")
    assert "needs https" in fr.arming(fresh=True).reason
    assert armed_openvault.calls == []

    fr.reset()
    monkeypatch.delenv("CORTEX_FREEROUTE_TOKEN")
    monkeypatch.setenv("OPENVAULT_BASE_URL", "http://vault.example.com")
    assert "needs an ov_ key" in fr.arming(fresh=True).reason
    assert armed_openvault.calls == []


def test_unchanged_switch_off_and_url_conflict(armed_openvault, monkeypatch) -> None:
    _install_local_hop(armed_openvault, pooled=3)
    monkeypatch.setenv("CORTEX_FREEROUTE", "0")
    arm = fr.arming(fresh=True)
    assert arm.armed is False
    assert "CORTEX_FREEROUTE=0" in arm.reason
    assert armed_openvault.calls == []

    monkeypatch.delenv("CORTEX_FREEROUTE")
    fr.reset()
    monkeypatch.setenv("CREW_OPENVAULT_URL", "http://127.0.0.1:5000")
    monkeypatch.setenv("OPENVAULT_BASE_URL", "http://127.0.0.1:5001")
    reason = fr.arming(fresh=True).reason
    assert "OPENVAULT_BASE_URL=http://127.0.0.1:5001" in reason
    assert "CREW_OPENVAULT_URL=http://127.0.0.1:5000" in reason
    assert armed_openvault.calls == []


# -- served_* from the OpenVault response only ---------------------------------


def test_served_fields_come_from_response_not_requested_model(armed_openvault) -> None:
    """Gate 5829081052: served_* stamped from the response. Would fail on old code."""
    _install_local_hop(armed_openvault, pooled=0)
    armed_openvault.reply(
        "SELECT 1 FROM t",
        model="ignored-openai-compat-id",
        served_provider=LOCAL_PROVIDER,
        served_model=LOCAL_MODEL,
        served_local=True,
    )
    out = fr.complete(
        "t",
        [{"role": "user", "content": "hi"}],
        pin="requested-not-the-source",
        accept=_sql_ok,
    )
    assert out.ok is True
    assert out.stamp is not None
    assert out.stamp.requested == "requested-not-the-source"
    assert out.stamp.served_provider == LOCAL_PROVIDER
    assert out.stamp.served_model == LOCAL_MODEL
    assert out.stamp.served_local is True
    assert out.stamp.served_reason == ""
    dumped = json.dumps(out.stamp.public())
    assert "requested-not-the-source" not in (
        json.dumps(out.stamp.served_provider) + json.dumps(out.stamp.served_model)
    )
    assert LOCAL_PROVIDER in dumped


def test_missing_served_fields_are_null_false_plus_reason(armed_openvault) -> None:
    armed_openvault.reply("SELECT 1 FROM t", model="ollama/qwen2.5")
    out = fr.complete("t", [{"role": "user", "content": "hi"}], accept=_sql_ok)
    assert out.ok is True
    assert out.stamp is not None
    assert out.stamp.served == "ollama/qwen2.5"
    assert out.stamp.served_provider is None
    assert out.stamp.served_model is None
    assert out.stamp.served_local is False
    assert ov_local.MISSING_PROVIDER in out.stamp.served_reason
    assert ov_local.MISSING_MODEL in out.stamp.served_reason
    assert ov_local.MISSING_LOCAL in out.stamp.served_reason


# -- Insights served_* from RouteStamp -----------------------------------------


def test_router_fingerprint_equals_route_stamp() -> None:
    stamp = fr.RouteStamp(
        call_id="eq-272",
        task="insights",
        requested="deepseek-v4-pro",
        served="openai/gpt-oss-120b",
        served_provider=LOCAL_PROVIDER,
        served_model=LOCAL_MODEL,
        served_local=True,
        served_reason="",
    )
    fp = fr.router_fingerprint(stamp)
    assert fp["served_provider"] == stamp.served_provider == LOCAL_PROVIDER
    assert fp["served_model"] == stamp.served_model == LOCAL_MODEL
    assert fp["served_local"] is stamp.served_local is True
    assert fp["served_reason"] == stamp.served_reason
    dumped = json.dumps(fp)
    assert "deepseek-v4-pro" not in dumped
    assert "gpt-oss-120b" not in dumped
    env: dict = {"status": "ABSTAIN", "values": []}
    fr.stamp_router_fingerprint(env, stamp)
    assert env["served_provider"] == LOCAL_PROVIDER
    assert env["served_model"] == LOCAL_MODEL
    assert env["served_local"] is True
    assert env["served_reason"] == ""


def test_router_fingerprint_empty_stamp_stays_null_false_plus_reason() -> None:
    empty = fr.RouteStamp(
        call_id="empty-272",
        task="insights",
        requested="deepseek-v4-pro",
        served="openai/gpt-oss-120b",
    )
    none_fp = fr.router_fingerprint()
    empty_fp = fr.router_fingerprint(empty)
    for fp in (none_fp, empty_fp):
        assert fp["served_provider"] is None
        assert fp["served_model"] is None
        assert fp["served_local"] is False
        assert fp["served_reason"] == fr.SERVED_PENDING_272
        dumped = json.dumps(fp)
        assert "deepseek-v4-pro" not in dumped
        assert "gpt-oss-120b" not in dumped
    env = insights.stamp_plan_source({"status": "REFUSE", "values": []})
    assert env["served_provider"] is None
    assert env["served_model"] is None
    assert env["served_local"] is False
    assert env["served_reason"] == fr.SERVED_PENDING_272


def test_insights_stamp_plan_source_equals_route_stamp() -> None:
    stamp = fr.RouteStamp(
        call_id="ins-272",
        task="insights",
        requested="ignored-requested",
        served="ignored-served",
        served_provider=LOCAL_PROVIDER,
        served_model=LOCAL_MODEL,
        served_local=True,
        served_reason="",
    )
    env = insights.stamp_plan_source(
        {"status": "ABSTAIN", "values": []},
        {"stamp": stamp.public()},
    )
    assert env["served_provider"] == stamp.served_provider
    assert env["served_model"] == stamp.served_model
    assert env["served_local"] is stamp.served_local
    assert env["served_reason"] == stamp.served_reason
    dumped = json.dumps(
        {
            "served_provider": env["served_provider"],
            "served_model": env["served_model"],
            "served_local": env["served_local"],
            "served_reason": env["served_reason"],
        }
    )
    assert "ignored-requested" not in dumped
    assert "ignored-served" not in dumped


# -- LOCAL_ONLY fail-closed (would return the cloud text on old code) ----------


def test_local_only_restriction_is_on_the_outgoing_request(armed_openvault, monkeypatch) -> None:
    _install_local_hop(armed_openvault, pooled=0)
    monkeypatch.setenv("CORTEX_FREEROUTE_LOCAL_ONLY", "1")
    armed_openvault.reply(
        "SELECT 1 FROM t",
        served_provider=LOCAL_PROVIDER,
        served_model=LOCAL_MODEL,
        served_local=True,
    )
    out = fr.complete("t", [{"role": "user", "content": "hi"}], accept=_sql_ok)
    assert out.ok is True
    assert armed_openvault.chat_calls
    assert armed_openvault.chat_calls[-1]["body"]["local_only"] is True
    assert fr.arming().public()["local_only"] is True


def test_local_only_drops_cloud_served_answer_text(armed_openvault, monkeypatch) -> None:
    """Gate 5829081052: LOCAL_ONLY refuses a cloud hop. Would fail on old code."""
    _install_local_hop(armed_openvault, pooled=3)
    monkeypatch.setenv("CORTEX_FREEROUTE_LOCAL_ONLY", "1")
    armed_openvault.reply(
        CLOUD_ANSWER,
        model="openai/gpt-oss-120b",
        served_provider="groq",
        served_model="openai/gpt-oss-120b",
        served_local=False,
    )
    out = fr.complete("t", [{"role": "user", "content": "hi"}])
    assert out.ok is False
    assert out.text == ""
    assert CLOUD_ANSWER not in (out.text or "")
    assert CLOUD_ANSWER not in (out.reason or "")
    assert out.message == {}
    assert "CORTEX_FREEROUTE_LOCAL_ONLY=1" in out.reason
    assert "did not serve locally" in out.reason
    assert out.stamp is not None
    assert out.stamp.served_local is False
    assert out.stamp.usable is False
    assert len(armed_openvault.chat_calls) == 1
    dumped = json.dumps(out.stamp.public())
    assert CLOUD_ANSWER not in dumped


def test_local_only_drops_answer_when_served_local_omitted(armed_openvault, monkeypatch) -> None:
    _install_local_hop(armed_openvault, pooled=0)
    monkeypatch.setenv("CORTEX_FREEROUTE_LOCAL_ONLY", "1")
    armed_openvault.reply(CLOUD_ANSWER, model=LOCAL_MODEL)
    out = fr.complete("t", [{"role": "user", "content": "hi"}])
    assert out.ok is False
    assert out.text == ""
    assert CLOUD_ANSWER not in (out.text or "")
    assert "CORTEX_FREEROUTE_LOCAL_ONLY=1" in out.reason
    assert out.stamp is not None and out.stamp.served_local is False


def test_local_only_no_local_hop_refuses_before_send(armed_openvault, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FREEROUTE_LOCAL_ONLY", "1")
    out = fr.complete("t", [{"role": "user", "content": "hi"}])
    assert out.ok is False
    assert out.text == ""
    assert "no local spendable hop" in out.reason
    assert "no cloud fallback" in out.reason
    assert armed_openvault.chat_calls == []


def _local_only_unavailable(fake, monkeypatch, named: str):
    _install_local_hop(fake, pooled=0)
    monkeypatch.setenv("CORTEX_FREEROUTE_LOCAL_ONLY", "1")
    fake.reply(
        status=503,
        error_type=ov_local.LOCAL_ONLY_UNAVAILABLE_TYPE,
        message="local-only request cannot be served; no local hop succeeded",
        error_reason=named,
    )
    out = fr.complete("t", [{"role": "user", "content": "hi"}])
    assert out.ok is False
    assert out.text == ""
    assert out.message == {}
    assert "CORTEX_FREEROUTE_LOCAL_ONLY=1" in out.reason
    assert ov_local.LOCAL_ONLY_UNAVAILABLE_TYPE in out.reason
    assert named in out.reason
    assert ov_local.VAULT_SEALED_TYPE not in out.reason
    assert len(fake.chat_calls) == 1
    assert fake.chat_calls[-1]["body"]["local_only"] is True
    return out


def test_local_only_unavailable_local_unreachable(armed_openvault, monkeypatch) -> None:
    _local_only_unavailable(armed_openvault, monkeypatch, ov_local.LOCAL_REASON_UNREACHABLE)


def test_local_only_unavailable_local_model_not_loaded(armed_openvault, monkeypatch) -> None:
    _local_only_unavailable(
        armed_openvault, monkeypatch, ov_local.LOCAL_REASON_MODEL_NOT_LOADED
    )


def test_local_only_unavailable_local_base_url_not_loopback(
    armed_openvault, monkeypatch
) -> None:
    _local_only_unavailable(
        armed_openvault, monkeypatch, ov_local.LOCAL_REASON_NOT_LOOPBACK
    )


def test_local_only_sealed_is_403_openvault_vault_sealed(
    armed_openvault, monkeypatch
) -> None:
    _install_local_hop(armed_openvault, pooled=0)
    monkeypatch.setenv("CORTEX_FREEROUTE_LOCAL_ONLY", "1")
    armed_openvault.reply(
        status=403,
        error_type=ov_local.VAULT_SEALED_TYPE,
        message="vault is sealed",
    )
    out = fr.complete("t", [{"role": "user", "content": "hi"}])
    assert out.ok is False
    assert out.text == ""
    assert out.message == {}
    assert "CORTEX_FREEROUTE_LOCAL_ONLY=1" in out.reason
    assert ov_local.VAULT_SEALED_TYPE in out.reason
    assert ov_local.LOCAL_ONLY_UNAVAILABLE_TYPE not in out.reason
    assert len(armed_openvault.chat_calls) == 1
