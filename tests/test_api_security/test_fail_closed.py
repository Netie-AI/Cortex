"""T2-FAILCLOSED (#263) on the engine auth port, and the suite's test-key plugin.

Engine-gated routes never accept a published ``dms-demo-*`` key or a template
placeholder, configured or not, and the refusal body is the same as for an
unknown key. Routes the DMS contract caller uses are not on the port yet and
are pinned unchanged here.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from CortexOS.security import auth_port
from packs.dms.security import api_auth
from packs.dms.security.request_authorizer import (
    PUBLISHED_KEYS,
    DmsRequestAuthorizer,
    is_unusable_key,
)
from tests.api_key_isolation import TEST_ADMIN_KEY, TEST_API_KEYS, TEST_VIEWER_KEY

_UNKNOWN_KEY_BODY = {"detail": "Valid API key required (X-API-Key or Bearer)"}


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    from CortexOS.execution import app_store

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("DMS_REFUSE_DEMO_KEYS", raising=False)
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    from CortexOS.api.app import create_app

    with TestClient(create_app()) as client:
        yield client


def _spend_app() -> TestClient:
    app = FastAPI()

    @app.post("/spend", dependencies=[Depends(auth_port.require_spend_auth())])
    async def spend() -> dict[str, bool]:
        return {"spent": True}

    return TestClient(app)


@pytest.mark.parametrize("key", sorted(PUBLISHED_KEYS))
def test_published_keys_never_authenticate_an_engine_route(app_client, monkeypatch, key):
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    for headers in ({"X-API-Key": key}, {"Authorization": f"Bearer {key}"}):
        res = app_client.get("/api/apps", headers=headers)
        assert res.status_code == 401
        assert res.json() == _UNKNOWN_KEY_BODY


def test_published_key_listed_in_dms_api_keys_is_still_refused(app_client, monkeypatch):
    monkeypatch.setenv("DMS_API_KEYS", "admin:dms-demo-admin-key")
    res = app_client.get("/api/apps", headers={"X-API-Key": "dms-demo-admin-key"})
    assert res.status_code == 401
    assert res.json() == _UNKNOWN_KEY_BODY


def test_placeholder_key_is_refused_even_when_configured(app_client, monkeypatch):
    monkeypatch.setenv("DMS_API_KEYS", "admin:replace_with_a_real_admin_key")
    res = app_client.get("/api/apps", headers={"X-API-Key": "replace_with_a_real_admin_key"})
    assert res.status_code == 401
    assert res.json() == _UNKNOWN_KEY_BODY


def test_published_key_cannot_spend(monkeypatch):
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    res = _spend_app().post("/spend", headers={"X-API-Key": "dms-demo-viewer-key"})
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == auth_port.SPEND_REQUIRES_AUTH


def test_no_configured_keys_means_only_an_openvault_token_gets_through(app_client, monkeypatch):
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    verified: list[str] = []

    def _fake_vault(token: str) -> api_auth.Caller:
        verified.append(token)
        return api_auth.Caller(role="viewer", actor="ov_k1")

    monkeypatch.setattr(api_auth, "_caller_from_openvault", _fake_vault)
    assert app_client.get("/api/apps").status_code == 401
    assert app_client.get("/api/apps", headers={"X-API-Key": "made-up"}).status_code == 401
    res = app_client.get("/api/apps", headers={"Authorization": "Bearer ov_realtokenxx"})
    assert res.status_code == 200 and res.json()["ok"] is True
    assert verified == ["ov_realtokenxx"]


def test_configured_keys_still_work(app_client, monkeypatch):
    monkeypatch.setenv("DMS_API_KEYS", TEST_API_KEYS)
    assert app_client.get("/api/apps", headers={"X-API-Key": TEST_VIEWER_KEY}).status_code == 200


def test_auth_disabled_stays_a_local_dev_opt_in(app_client, monkeypatch):
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    assert app_client.get("/api/apps").status_code == 200
    assert app_client.get("/api/apps", headers={"X-API-Key": "dms-demo-viewer-key"}).status_code == 200


def test_unusable_key_rule():
    assert all(is_unusable_key(k) for k in PUBLISHED_KEYS)
    assert is_unusable_key("REPLACE_WITH_viewer")
    assert not is_unusable_key(TEST_VIEWER_KEY)
    assert not is_unusable_key("ov_realtokenxx")


# --- the DMS contract caller is not on the port: unchanged -------------------


def test_dms_routes_still_accept_the_demo_viewer_key(monkeypatch):
    """DMS defaults to dms-demo-viewer-key; api_auth-gated routes keep accepting it."""
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    monkeypatch.delenv("DMS_REFUSE_DEMO_KEYS", raising=False)
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    app = FastAPI()

    @app.get("/dms-style")
    async def dms_style(caller: api_auth.Caller = Depends(api_auth.require_role("viewer"))):
        return {"role": caller.role}

    res = TestClient(app).get("/dms-style", headers={"X-API-Key": "dms-demo-viewer-key"})
    assert res.status_code == 200 and res.json() == {"role": "viewer"}


# --- tests/api_key_isolation.py -------------------------------------------------


def test_plugin_configures_the_test_keys():
    import os

    assert os.environ["DMS_API_KEYS"] == TEST_API_KEYS


def test_plugin_registers_the_dms_authorizer_under_the_suite_pack():
    assert isinstance(auth_port.registered_authorizer(), DmsRequestAuthorizer)
    client = _spend_app()
    assert client.post("/spend").status_code == 401
    assert client.post("/spend", headers={"X-API-Key": TEST_VIEWER_KEY}).json() == {"spent": True}
    assert client.post("/spend", headers={"X-API-Key": TEST_ADMIN_KEY}).json() == {"spent": True}
