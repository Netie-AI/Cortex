"""Cortex #269: Insights served_* + setup fingerprint. Stubbed transport only."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import insights
from CortexOS.integrations import freeroute as core
from packs.dms.security.rate_limit import reset_limiter
from tests.test_crew.test_insights import ScriptedBridge, _certified_engine


def _assert_unproven_served(env: dict[str, Any], *, requested: str = "") -> None:
    assert env["served_provider"] is None
    assert env["served_model"] is None
    assert env["served_local"] is False
    reason = str(env.get("served_reason") or "")
    pending = "272" in reason and "not inferred" in reason.lower()
    omitted = "omitted served_provider" in reason
    assert pending or omitted, reason
    dumped = json.dumps(env)
    if requested:
        assert env["served_model"] != requested
    assert "served_provider" in dumped
    assert env.get("learn_source") in {"env", "default"}
    assert "learn_enabled" in env
    assert env.get("route_store_id")
    assert env.get("masking_state") == "on"
    text = insights.render_tool_text(env)
    assert "served_provider: None" in text
    assert "served_model: None" in text
    assert "served_local: False" in text
    assert "learn_source:" in text
    assert "route_store_id:" in text
    if requested:
        assert f"served_model: {requested}" not in text


@pytest.mark.asyncio
async def test_certified_insights_stamp_served_null_with_reason() -> None:
    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights("how many skus", bridge=bridge, ask=True)
    assert env["status"] == "CERTIFIED"
    assert env["values"] == [{"sku_count": 12}]
    assert "12" in (env.get("answer") or "")
    _assert_unproven_served(env)


@pytest.mark.asyncio
async def test_generate_unarmed_refusal_stamps_fingerprint() -> None:
    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights(
        "how many skus", bridge=bridge, ask=False, generate=True
    )
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    _assert_unproven_served(env)


@pytest.mark.asyncio
async def test_generate_success_does_not_copy_requested_or_route_model(
    monkeypatch,
) -> None:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {
            "ok": True,
            "armed": True,
            "vault_live": True,
            "local_keys": False,
            "cloud_keys": True,
            "detail": "vault-armed",
            "live_5000_ci": False,
        },
    )

    async def fake_complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs, prompt
        return {
            "ok": True,
            "text": "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "deepseek", "model": "deepseek-chat"},
            "stamp": {
                "requested": "deepseek-v4-pro",
                "served": "openai/gpt-oss-120b",
                "line": "asked deepseek-v4-pro, served openai/gpt-oss-120b",
            },
        }

    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights(
        "how many skus",
        bridge=bridge,
        ask=False,
        generate=True,
        complete=fake_complete,
    )
    assert env["status"] == "ABSTAIN"
    assert env["values"] == []
    assert "inventory" in (env.get("query_sql") or env.get("generative", {}).get("sql") or "").lower()
    _assert_unproven_served(env, requested="deepseek-v4-pro")
    dumped = json.dumps(
        {
            "served_provider": env["served_provider"],
            "served_model": env["served_model"],
            "served_local": env["served_local"],
            "served_reason": env["served_reason"],
        }
    )
    assert "deepseek" not in dumped
    assert "gpt-oss" not in dumped


@pytest.mark.asyncio
async def test_armed_generate_stubbed_transport_keeps_served_null(
    armed_openvault, monkeypatch
) -> None:
    monkeypatch.setenv("CORTEX_FREEROUTE_MODELS", "deepseek-v4-pro")
    core.reset()
    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights(
        "how many skus", bridge=bridge, ask=False, generate=True
    )
    assert env["values"] == []
    assert env["status"] in {"ABSTAIN", "REFUSE"}
    _assert_unproven_served(env, requested="deepseek-v4-pro")
    text = insights.render_tool_text(env)
    assert "served_model: deepseek-v4-pro" not in text
    assert "served_model: openai/gpt-oss-120b" not in text
    if env.get("generative"):
        stamp = (env["generative"] or {}).get("stamp") or {}
        if stamp.get("requested"):
            assert env["served_model"] != stamp.get("requested")
        if stamp.get("served"):
            assert env["served_model"] != stamp.get("served")


def test_http_insights_and_401_carry_fingerprint(
    monkeypatch, tmp_path, armed_openvault
) -> None:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    with TestClient(create_app()) as client:
        refused = client.post(
            "/v1/insights",
            json={"intent": "what is our ARR", "ask": True},
        )
        assert refused.status_code == 200, refused.text
        body = refused.json()
        assert body["status"] == "REFUSE"
        assert body["values"] == []
        _assert_unproven_served(body)

    with TestClient(create_app(), client=("10.0.0.5", 5555)) as remote:
        res = remote.post(
            "/v1/insights",
            json={"intent": "how many skus", "ask": False, "generate": True},
        )
        assert res.status_code == 401, res.text
        env = res.json()
        assert env["status"] == "REFUSE"
        assert env["values"] == []
        # Cortex #275: an unauthenticated 401 carries the reason code and a
        # message only; no setup fingerprint, learn state or route store.
        assert env["reason"] == "generate_requires_bearer"
        for key in ("route_store", "route_store_id", "learn_enabled", "learn_source", "served_reason"):
            assert key not in env, key
