"""F7 remainder — API-key RBAC and rate limiting tests."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from packs.dms.security.api_auth import parse_api_keys
from packs.dms.security.rate_limit import reset_limiter


@pytest.fixture
def api_keys_env(monkeypatch):
    monkeypatch.setenv(
        "DMS_API_KEYS",
        "viewer:sk-viewer-test;steward:sk-steward-test;admin:sk-admin-test",
    )
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("PACK", "dms")
    return {
        "viewer": "sk-viewer-test",
        "steward": "sk-steward-test",
        "admin": "sk-admin-test",
    }


@pytest.fixture
def dms_client(api_keys_env, monkeypatch):
    reset_limiter(per_minute=120)
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_parse_api_keys_maps_roles():
    callers = parse_api_keys("viewer:a;steward:b;admin:c")
    assert callers["a"].role == "viewer"
    assert callers["b"].role == "steward"
    assert callers["c"].role == "admin"


def test_skills_config_requires_api_key(dms_client):
    res = dms_client.post("/dms/skills/config", json={"enabled": True})
    assert res.status_code == 401


def test_viewer_cannot_toggle_capture(dms_client, api_keys_env):
    res = dms_client.post(
        "/dms/skills/config",
        json={"enabled": True},
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 403


def test_steward_can_toggle_capture(dms_client, api_keys_env):
    res = dms_client.post(
        "/dms/skills/config",
        json={"enabled": False},
        headers={"X-API-Key": api_keys_env["steward"]},
    )
    assert res.status_code == 200
    assert res.json()["capture_enabled"] is False


def test_viewer_can_list_skills(dms_client, api_keys_env):
    res = dms_client.get(
        "/dms/skills",
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_deactivate_requires_steward(dms_client, api_keys_env):
    res = dms_client.post(
        "/dms/skills/nonexistent-id/deactivate",
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 403


def test_invalid_key_rejected(dms_client, api_keys_env):
    res = dms_client.get("/dms/skills", headers={"X-API-Key": "not-a-real-key"})
    assert res.status_code == 401


def test_rate_limit_429(dms_client, api_keys_env, monkeypatch):
    reset_limiter(per_minute=2)
    headers = {"X-API-Key": api_keys_env["viewer"]}
    assert dms_client.get("/dms/skills", headers=headers).status_code == 200
    assert dms_client.get("/dms/skills", headers=headers).status_code == 200
    assert dms_client.get("/dms/skills", headers=headers).status_code == 429
    reset_limiter(per_minute=120)
