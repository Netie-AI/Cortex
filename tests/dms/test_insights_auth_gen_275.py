"""Cortex #275 ROUTER-1a / AUTH-GEN-01 on POST /v1/insights (OpenVault path).

- generate=true without the caller's own ov_ bearer is refused before any
  provider call, loopback 127.0.0.1 included (the exact path that leaked on
  prove), with the named reason ``generate_requires_bearer``;
- Cortex's own armed key is never lent to a relayed call;
- the unauthenticated 401 body carries no path and no learn/setup state, and
  authenticated bodies carry no filesystem path;
- caller-supplied ``ontology`` values never reach a prompt (pin of the drop).

Stand-in OpenVault only (tests/conftest.py ``armed_openvault``). No network.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.integrations import freeroute
from packs.dms.security.rate_limit import reset_limiter

CALLER = "ov_" + "CallerKey7Hq3Lm8Rt5Wy2"
CORTEX_KEY = "ov_" + "CortexOwnKey9Zp4Vx6Tn1"
SEEDED = ("SEEDVAL-ALPHA-7781", "seed-bravo-4410@x.example", "SEEDVAL-CHARLIE-0092")


@pytest.fixture()
def app(armed_openvault, monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    return create_app()


def _certified(monkeypatch) -> None:
    from CortexOS.crew.engine_bridge import LocalEngineBridge

    async def fake_ask(self, question: str) -> dict[str, Any]:  # noqa: ARG001
        return {
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

    monkeypatch.setattr(LocalEngineBridge, "ask", fake_ask)


_ABS_PATH = re.compile(r"(?:^|[\s'\"(=])(?:/(?:home|tmp|root|usr|var|opt|mnt|Users)/|[A-Za-z]:\\\\)")


def _assert_no_filesystem_path(body: Any, *, tmp_path) -> None:
    raw = json.dumps(body)
    assert str(tmp_path) not in raw
    assert str(freeroute.store_path()) not in raw
    assert not _ABS_PATH.search(raw), raw[:600]

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            assert "path" not in node, node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(body)


def _assert_refused_401(res, *, tmp_path) -> None:
    assert res.status_code == 401, res.text
    body = res.json()
    assert body["status"] == "REFUSE"
    assert body["reason"] == "generate_requires_bearer"
    assert body["values"] == []
    assert body["refused"]
    for key in ("route_store", "route_store_id", "learn_enabled", "learn_source", "masking_state"):
        assert key not in body, key
    _assert_no_filesystem_path(body, tmp_path=tmp_path)


def test_loopback_generate_without_authorization_is_401_with_zero_provider_calls(
    app, armed_openvault, monkeypatch, tmp_path
) -> None:
    _certified(monkeypatch)
    with TestClient(app, client=("127.0.0.1", 5555)) as local:
        res = local.post("/v1/insights", json={"intent": "how many skus", "ask": True, "generate": True})
        _assert_refused_401(res, tmp_path=tmp_path)
        assert len(armed_openvault.chat_calls) == 0

        # Ranking without generation still serves the same caller.
        ranked = local.post("/v1/insights", json={"intent": "how many skus", "ask": True})
    assert ranked.status_code == 200, ranked.text
    env = ranked.json()
    assert env["status"] == "CERTIFIED"
    assert env["values"] == [{"sku_count": 12}]
    assert "12" in (env.get("answer") or "")
    assert len(armed_openvault.chat_calls) == 0


@pytest.mark.parametrize(
    "headers",
    [{"Authorization": "Bearer "}, {"Authorization": ""}, {"X-API-Key": "pytest-viewer-key"}],
)
def test_armed_cortex_key_is_never_lent_to_an_empty_relayed_bearer(
    app, armed_openvault, monkeypatch, tmp_path, headers
) -> None:
    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", CORTEX_KEY)
    armed_openvault.identities[CORTEX_KEY] = "key_cortex"
    freeroute.reset()
    assert freeroute.arming().armed
    with TestClient(app, client=("127.0.0.1", 5555)) as local:
        res = local.post(
            "/v1/insights",
            json={"intent": "how many skus", "ask": False, "generate": True},
            headers=headers,
        )
    _assert_refused_401(res, tmp_path=tmp_path)
    assert len(armed_openvault.chat_calls) == 0
    assert CORTEX_KEY not in res.text


@pytest.mark.asyncio
async def test_anonymous_runner_refuses_if_freeroute_arms_mid_request(armed_openvault) -> None:
    from CortexOS.crew import freeroute as crew_fr
    from CortexOS.insights import routes

    runner = routes._anonymous_complete(crew_fr.complete)
    out = await runner(None, purpose="think", prompt="how many skus", bearer=None)
    assert out["reason"] == "generate_requires_bearer"
    assert out["values"] == []
    assert len(armed_openvault.chat_calls) == 0


def test_authenticated_generate_body_has_no_filesystem_path_and_pins_the_ontology_drop(
    app, armed_openvault, monkeypatch, tmp_path
) -> None:
    _certified(monkeypatch)
    armed_openvault.identities[CALLER] = "key_caller"
    armed_openvault.default_content = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
    with TestClient(app, client=("127.0.0.1", 5555)) as local:
        res = local.post(
            "/v1/insights",
            json={
                "intent": "how many skus",
                "ask": True,
                "generate": True,
                # DMS sends raw sample values here; Cortex must never prompt with them.
                "ontology": {"values": list(SEEDED), "samples": {"sku": SEEDED[0]}},
                "sample_values": [SEEDED[2]],
                "context": f"bound values: {SEEDED[1]}",
            },
            headers={"Authorization": f"Bearer {CALLER}"},
        )
    assert res.status_code == 200, res.text
    env = res.json()
    # think and generative_ask both went out, relayed with the caller's key.
    assert len(armed_openvault.chat_calls) >= 2
    for call in armed_openvault.chat_calls:
        assert call["headers"].get("Authorization") == f"Bearer {CALLER}"
        sent = json.dumps(call["body"])
        for value in SEEDED:
            assert value not in sent
    assert env.get("route_store_id")
    assert "learn_enabled" in env
    _assert_no_filesystem_path(env, tmp_path=tmp_path)
    assert CALLER not in res.text
