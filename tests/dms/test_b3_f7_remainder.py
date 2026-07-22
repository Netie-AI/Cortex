"""B3 — secure_reversible adoption + brain RBAC smoke."""
from __future__ import annotations

import os

import pytest


def test_secure_message_flag_off_matches_harness():
    from packs.dms import secure_message
    from packs.dms.security.prompt_harness import secure_for_prompt

    os.environ.pop("DMS_REVERSIBLE_PII", None)
    text = "Contact alice@example.com about stock"
    a = secure_message(text, block_scam=False)
    b = secure_for_prompt(text, block_injection=True, block_scam=False)
    assert a["blocked"] == b.blocked
    assert a["safe_text"] == b.safe_text
    assert a["reversible"] is False


def test_secure_message_flag_on_masks_email(monkeypatch):
    from packs.dms import secure_message

    monkeypatch.setenv("DMS_REVERSIBLE_PII", "1")
    r = secure_message("Email bob@corp.test please", block_scam=False)
    assert r["blocked"] is False
    assert "bob@corp.test" not in r["safe_text"]
    assert r["reversible"] is True
    assert "NETIE_" in r["safe_text"] or "[REDACTED" in r["safe_text"]


def test_brain_requires_api_key(monkeypatch):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    try:
        from fastapi.testclient import TestClient
    except Exception:
        pytest.skip("fastapi unavailable")
    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    assert client.post("/dms/brain/chart", json={"query": "x"}).status_code == 401
    ok = client.post(
        "/dms/brain/chart",
        json={"query": "stock"},
        headers={"X-API-Key": "dms-demo-viewer-key"},
    )
    assert ok.status_code == 200


def test_memory_requires_api_key(monkeypatch):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    try:
        from fastapi.testclient import TestClient
    except Exception:
        pytest.skip("fastapi unavailable")
    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    assert client.get("/api/memory/stats").status_code == 401
    assert client.get(
        "/api/memory/stats",
        headers={"X-API-Key": "dms-demo-viewer-key"},
    ).status_code == 200
