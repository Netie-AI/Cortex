"""API + MCP surface for Find Skills discovery."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_discovery_sources(client):
    res = client.get("/api/discovery/sources")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    ids = {s["id"] for s in data["sources"]}
    assert "punkpeye/awesome-mcp-servers" in ids
    assert "microsoft/SkillOpt" in ids


def test_find_skills_endpoint(client):
    res = client.post(
        "/api/discovery/find-skills",
        json={"goal": "playwright browser testing", "top_k": 5},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["best"]["name"]
    assert "Are there any good skills for" in data["question"]


def test_find_mcp_endpoint(client):
    res = client.post("/api/discovery/find-mcp", json={"goal": "sqlite database", "top_k": 3})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["matches"]


def test_mcp_catalog_includes_find_skills(client):
    res = client.get("/mcp/tools")
    assert res.status_code == 200
    names = {t["name"] for t in res.json()["tools"]}
    assert "find_skills" in names
    assert "find_mcp" in names
    assert "find_subagents" in names


def test_mcp_call_find_skills(client):
    res = client.post(
        "/mcp/call",
        json={"name": "find_skills", "arguments": {"goal": "PDF document extraction", "top_k": 3}},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["result"]["best"] is not None
