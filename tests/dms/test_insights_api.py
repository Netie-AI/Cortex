"""Cortex #213: stable /v1/insights API for DMS generative-ask and AirGPT skin.

Contract + refuse paths. Same run_insights as Crew. Does not invent live
:5000 / :8020 green. Does not invent CoT COMPLETE or climb %.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packs.dms.security.rate_limit import reset_limiter


def _certified_engine(**extra: Any) -> dict[str, Any]:
    body = {
        "ok": True,
        "answer": "There are 12 skus.",
        "badge": "governed_metric",
        "layer": "governed_metric",
        "metric_id": "sku_count",
        "audit_id": "audit-sku",
        "row_count": 1,
        "rows": [{"sku_count": 12}],
        "sources": ["inventory"],
        "sql_used": "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
        "truncated": False,
    }
    body.update(extra)
    return body


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_law_is_certified_abstain_refuse_and_names_consumers(api_client) -> None:
    res = api_client.get("/v1/insights")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["statuses"] == ["CERTIFIED", "ABSTAIN", "REFUSE"]
    assert body["stable"] == "POST /v1/insights"
    assert body["consumers"]["dms"].startswith("POST /v1/insights")
    assert "airgpt" in body["consumers"]
    assert body["live_5000_ci"] is False
    assert body["cot_climb"]["complete"] is False
    assert body["cot_climb"]["status"] == "INCOMPLETE"
    assert body["cot_climb"]["replaces_baseline"] is False
    assert body["measured_baseline"]["gen"] == "57.69%"
    assert body["measured_baseline"]["exact"] == "38.46%"
    assert body["measured_baseline"]["wrong"] == 0
    assert "not COMPLETE" in body["scale"]
    raw = json.dumps(body)
    assert "LIVE_KEY" not in raw
    assert "ov_" not in raw
    assert "sk-" not in raw
    assert "gsk_" not in raw


def test_identity_returns_no_token(api_client) -> None:
    body = api_client.get("/v1/insights/identity").json()
    assert body["identity"] == "cortex:crew"
    assert body["mint"] is False
    assert body["token_returned"] is False
    assert body["custody"] == "openvault"
    assert body["live_5000_ci"] is False
    assert body["http"]["stable"] == "GET /v1/insights/identity"
    assert body["measured_baseline"]["gen"] == "57.69%"
    raw = json.dumps(body)
    assert "LIVE_KEY" not in raw
    assert "ov_" not in raw


def test_keys_document_local_vs_cloud_without_secrets(api_client) -> None:
    body = api_client.get("/v1/insights/keys").json()
    assert body["custody"] == "openvault"
    assert body["second_vault"] is False
    assert body["callers_hold_provider_keys"] is False
    assert body["live_key_rotate"] is False
    assert body["live_5000_ci"] is False
    assert body["token_returned"] is False
    assert "local_keys" in body
    assert "cloud_keys" in body
    assert "ov_" in (body.get("note") or "")
    raw = json.dumps(body)
    assert "LIVE_KEY" not in raw
    assert "gsk_" not in raw


def test_ontology_ranks_before_ask_and_carries_no_values(api_client) -> None:
    res = api_client.get("/v1/insights/ontology", params={"q": "how many skus"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["phase"] == "ontology"
    assert body["ontology"]["locations"][0]["where"]["table"]
    assert body["ontology"]["metrics"]
    assert body.get("values") is None
    assert body["live_5000_ci"] is False
    missing = api_client.get("/v1/insights/ontology")
    assert missing.status_code == 400


def test_empty_intent_is_400(api_client) -> None:
    empty = api_client.post("/v1/insights", json={"intent": ""})
    assert empty.status_code == 400


def test_unknown_intent_refuses_without_invented_numbers(api_client) -> None:
    res = api_client.post("/v1/insights", json={"intent": "what is our ARR", "ask": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "REFUSE"
    assert body["values"] == []
    assert "ARR" in (body.get("answer") or "") or "no ontology" in (body.get("answer") or "").lower()
    assert body["api"]["live_5000_ci"] is False
    assert body["api"]["cot_climb_complete"] is False
    text = body.get("answer") or ""
    assert "999" not in text


def test_generate_unarmed_refuses_and_does_not_invent_numbers(api_client) -> None:
    res = api_client.post(
        "/v1/insights",
        json={"intent": "how many skus", "ask": False, "generate": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "REFUSE"
    assert body["values"] == []
    assert body["generative"]["ok"] is False
    assert body["generative"]["values"] == []
    reason = (body.get("answer") or "") + str(body.get("generative"))
    assert (
        "unarmed" in reason.lower()
        or "invent-green" in reason.lower()
        or "CORTEX_FREEROUTE" in reason
        or "CREW_OPENVAULT" in reason
        or "disabled" in reason.lower()
    )
    assert "999" not in (body.get("answer") or "")
    climb = body["generative"].get("climb") or {}
    assert climb.get("complete") is False


def test_question_alias_matches_intent(api_client) -> None:
    res = api_client.post("/v1/insights", json={"question": "what is our ARR", "ask": True})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "REFUSE"
    assert res.json()["values"] == []


def test_relay_bearer_never_lends_the_cortex_key(monkeypatch) -> None:
    from CortexOS.insights import keys as insight_keys

    monkeypatch.delenv("OPENVAULT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENVAULT_URL", raising=False)
    monkeypatch.delenv("CREW_OPENVAULT_URL", raising=False)
    assert insight_keys.relay_bearer("Bearer ov_callerkeyxx", "10.0.0.5") == "ov_callerkeyxx"
    assert insight_keys.relay_bearer("", "10.0.0.5") is None
    assert insight_keys.relay_bearer("", "testclient") is None
    assert insight_keys.relay_bearer("", "127.0.0.1") == ""
    assert insight_keys.peer_is_loopback("127.0.0.1") is True
    assert insight_keys.peer_is_loopback("testclient") is False


def test_key_posture_splits_local_and_cloud_hops_without_secrets(armed_openvault) -> None:
    from CortexOS.insights import keys as insight_keys
    from CortexOS.integrations import freeroute as fr

    pose = insight_keys.key_posture()
    assert pose["custody"] == "openvault"
    assert pose["second_vault"] is False
    assert pose["callers_hold_provider_keys"] is False
    assert pose["live_key_rotate"] is False
    assert pose["live_5000_ci"] is False
    assert pose["token_returned"] is False
    assert pose["cloud_keys"] is True
    assert "groq" in pose["cloud_hops"]
    armed_openvault.hops = [armed_openvault.hop("ollama", 5), armed_openvault.hop("groq", 10)]
    fr.reset()
    pose = insight_keys.key_posture()
    assert pose["local_keys"] is True
    assert "ollama" in pose["local_hops"]
    assert pose["cloud_keys"] is True
    raw = json.dumps(pose)
    assert "gsk_" not in raw
    assert "LIVE_KEY" not in raw


def test_certified_ask_uses_local_bridge_and_returns_rows(api_client, monkeypatch) -> None:
    from CortexOS.crew.engine_bridge import LocalEngineBridge

    async def fake_ask(self, question: str) -> dict[str, Any]:  # noqa: ARG001
        return _certified_engine()

    monkeypatch.setattr(LocalEngineBridge, "ask", fake_ask)
    res = api_client.post("/v1/insights", json={"intent": "how many skus", "ask": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "CERTIFIED"
    assert body["values"] == [{"sku_count": 12}]
    assert "12" in (body.get("answer") or "")
    assert body["validation"]["include"]
    assert body["api"]["consumer"] == "dms"
    assert body["api"]["stable"] == "POST /v1/insights"


def test_armed_generate_without_caller_key_is_401(armed_openvault, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    with TestClient(create_app(), client=("10.0.0.5", 5555)) as remote:
        res = remote.post(
            "/v1/insights",
            json={"intent": "how many skus", "ask": False, "generate": True},
        )
        assert res.status_code == 401, res.text
        body = res.json()
        assert body["status"] == "REFUSE"
        assert body["values"] == []
        assert "needs its own OpenVault ov_ key" in body["refused"]
        assert armed_openvault.chat_calls == []


def test_airgpt_sidecar_requires_key_and_shares_the_same_path(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv(
        "DMS_API_KEYS",
        "viewer:sk-viewer-test;steward:sk-steward-test;admin:sk-admin-test",
    )
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    denied = client.get("/dms/sidecar/insights")
    assert denied.status_code == 401
    law = client.get("/dms/sidecar/insights", headers={"X-API-Key": "sk-viewer-test"})
    assert law.status_code == 200, law.text
    body = law.json()
    assert body["stable"] == "POST /v1/insights"
    assert body["statuses"] == ["CERTIFIED", "ABSTAIN", "REFUSE"]
    asked = client.post(
        "/dms/sidecar/insights",
        json={"intent": "what is our ARR", "ask": True},
        headers={"X-API-Key": "sk-viewer-test"},
    )
    assert asked.status_code == 200, asked.text
    env = asked.json()
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert env["api"]["consumer"] == "airgpt"
    assert env["api"]["alias"] == "POST /dms/sidecar/insights"
    assert "999" not in (env.get("answer") or "")
