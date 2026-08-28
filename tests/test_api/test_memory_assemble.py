"""Memory plane HTTP surface."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient


def test_memory_assemble_endpoint(monkeypatch):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    from netie.memory.store import MemoryRecord

    from CortexOS.api.app import create_app
    from CortexOS.api.memory_routes import _STORE

    _STORE.upsert([MemoryRecord(id="m1", text="warehouse aisle 3", vector=[1.0, 0.0])])
    with TestClient(create_app()) as client:
        res = client.post(
            "/api/memory/assemble",
            json={"vector": [1.0, 0.0], "k": 3, "session_id": "sess-1"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "warehouse aisle 3" in body["text_blob"]
    assert body["hits"][0]["id"] == "m1"
    assert body["layers"]["vector_count"] == 1
