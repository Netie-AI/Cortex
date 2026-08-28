"""API smoke for /api/engine/thesis|just-works|bakeoff."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_thesis(client):
    res = client.get("/api/engine/thesis")
    assert res.status_code == 200
    data = res.json()
    assert data["thesis"]["role"] == "lubricant"
    assert data["backends"]


def test_just_works(client):
    res = client.post(
        "/api/engine/just-works",
        json={"hardware": {"vram_gb": 0, "nvidia": {"present": False}}},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["config"]["backend"] == "ollama"
    assert data["applied"] is False


def test_bakeoff(client, monkeypatch):
    monkeypatch.setattr(
        "CortexOS.engine.bakeoff.probe_backend",
        lambda backend_id: {
            "id": backend_id,
            "name": backend_id,
            "ok": False,
            "health_ms": None,
            "needs_gpu": False,
            "install_how": "native",
            "error": "offline",
        },
    )
    res = client.post("/api/engine/bakeoff", json={"write_report": False})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert res.json()["live_count"] == 0
