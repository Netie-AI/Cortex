"""DMS demo-key callers stay reachable; engine-gated routes fail closed (T2-FAILCLOSED, #263).

Netie-AI/dms main (288790bd) still defaults its Cortex credential to the
published ``dms-demo-viewer-key``:

* ``apps/api/dms_api/settings.py:36`` ``cortex_api_key``, used by ``CortexClient``
  (contract, insights, ``/dms/query``), ``routes/trust.py`` and ``routes/ontology.py``;
* ``apps/api/dms_api/cortex_read.py:27`` ``DEFAULT_VIEWER_KEY``, the ``cortex_get`` fallback;
* ``packages/cortex_client/cortex_client/insights.py:21`` ``DEMO_VIEWER_KEY``, sent
  on ``/v1/insights``; the client itself refuses ``generate=true`` with it.

DMS sends the key as both ``X-API-Key`` and ``Authorization: Bearer``
(``cortex_client.insights.auth_headers``). Every route below must answer that
caller exactly as it answers with auth disabled. If one of them is moved behind
the engine auth port before DMS carries an OpenVault-issued key, this fails.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from CortexOS.security import auth_port
from tests.api_key_isolation import TEST_VIEWER_KEY

DEMO_KEY = "dms-demo-viewer-key"
DMS_HEADERS = {"X-API-Key": DEMO_KEY, "Authorization": f"Bearer {DEMO_KEY}"}
_UNKNOWN_KEY_BODY = {"detail": "Valid API key required (X-API-Key or Bearer)"}

# (method, path, json body, expected status). Contract POSTs send {} on purpose:
# a FastAPI auth dependency refuses before body validation, so 422 proves the
# auth layer let the caller through without running the engine.
DMS_CALLER_ROUTES: list[tuple[str, str, Any, int]] = [
    ("POST", "/v1/contract/ask", {}, 422),
    ("POST", "/v1/contract/submit", {}, 422),
    ("POST", "/v1/contract/ledger/append", {}, 422),
    ("POST", "/v1/contract/ledger/verify", {}, 200),
    ("POST", "/v1/contract/drillthrough", {}, 422),
    ("GET", "/v1/contract/tools", None, 200),
    ("POST", "/v1/contract/jwks/refresh", None, 200),
    ("GET", "/v1/insights", None, 200),
    ("GET", "/v1/insights/keys", None, 200),
    ("GET", "/v1/insights/identity", None, 200),
    ("GET", "/v1/insights/ontology?q=stock", None, 200),
    ("POST", "/v1/insights", {"intent": "stock by sku", "generate": False}, 200),
    ("POST", "/dms/query", {"question": "total stock on hand"}, 200),
    # trust.py and ontology.py read these; Cortex main does not serve them, so
    # DMS already gets a 404 here. Pinned so a future gate is noticed.
    ("GET", "/dms/eval/summary", None, 404),
    ("GET", "/dms/eval/runs/x", None, 404),
    ("GET", "/dms/ontology", None, 404),
    ("GET", "/dms/ontology/graph", None, 404),
]


@pytest.fixture
def client(monkeypatch, tmp_path):
    from CortexOS.execution import app_store, submit

    # Cortex as DMS meets it by default: auth on, no DMS_API_KEYS, demo fallback live.
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    monkeypatch.delenv("DMS_REFUSE_DEMO_KEYS", raising=False)
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    # refresh_jwks replaces the process-wide manifest trust store.
    monkeypatch.setattr(submit, "refresh_jwks", lambda: {"ok": True, "stub": True})
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    from CortexOS.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.mark.parametrize(("method", "path", "body", "status"), DMS_CALLER_ROUTES)
def test_dms_demo_key_caller_is_served_as_with_auth_off(client, monkeypatch, method, path, body, status):
    res = client.request(method, path, json=body, headers=DMS_HEADERS)
    assert res.status_code == status, (method, path, res.status_code, res.text[:300])
    assert auth_port.SPEND_REQUIRES_AUTH not in res.text
    assert auth_port.NO_AUTHORIZER_DETAIL not in res.text

    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    baseline = client.request(method, path, json=body, headers=DMS_HEADERS)
    assert baseline.status_code == res.status_code, (method, path)


def _port_gated_paths(client: TestClient) -> list[tuple[str, str]]:
    out = []
    for path, ops in client.app.openapi()["paths"].items():
        if path.startswith(("/api/apps", "/api/connectors")):
            concrete = path.replace("{app_id}", "x").replace("{agent_id}", "constructor").replace(
                "{chat_id}", "c1"
            )
            out.extend((m.upper(), concrete) for m in ops)
    return sorted(out)


def test_engine_gated_routes_refuse_the_dms_demo_key(client):
    gated = _port_gated_paths(client)
    assert len(gated) >= 20
    for method, path in gated:
        res = client.request(method, path, json={}, headers=DMS_HEADERS)
        assert res.status_code == 401, (method, path, res.status_code)
        assert res.json() == _UNKNOWN_KEY_BODY, (method, path)


def test_engine_gated_route_serves_a_configured_key(client, monkeypatch):
    monkeypatch.setenv("DMS_API_KEYS", f"viewer:{TEST_VIEWER_KEY}")
    assert client.get("/api/apps", headers={"X-API-Key": TEST_VIEWER_KEY}).status_code == 200
    assert client.get("/api/apps", headers=DMS_HEADERS).status_code == 401


def test_spend_route_refuses_the_dms_demo_key(client):
    app = FastAPI()

    @app.post("/spend", dependencies=[Depends(auth_port.require_spend_auth())])
    async def spend() -> dict[str, bool]:
        return {"spent": True}

    res = TestClient(app).post("/spend", headers=DMS_HEADERS)
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == auth_port.SPEND_REQUIRES_AUTH
