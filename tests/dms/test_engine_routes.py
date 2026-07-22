"""E0 engine registry API — AirGPT Hosting (i) / config contract."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def engine_client(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.chdir(tmp_path)
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_engine_specs_ok(engine_client):
    res = engine_client.get("/api/engine/specs")
    assert res.status_code == 200
    data = res.json()
    assert "backends" in data or "optimizers" in data or "config" in data


def test_engine_backends_ok(engine_client):
    res = engine_client.get("/api/engine/backends")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert any(b["id"] == "ollama" for b in data["backends"])


def test_engine_config_roundtrip(engine_client):
    res = engine_client.post(
        "/api/engine/config",
        json={"backend": "ollama", "agents_runtime": "cortex", "research": False},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["config"]["backend"] == "ollama"
