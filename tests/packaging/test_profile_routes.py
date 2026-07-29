"""Route table identical across profiles; behaviour differs (200/real vs 501)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SHARED_PATHS = (
    ("POST", "/run"),
    ("POST", "/search"),
    ("POST", "/api/engine/auto"),
    ("GET", "/api/routines"),
    ("GET", "/api/goals"),
    ("GET", "/health/features"),
    ("POST", "/v1/contract/ask"),
    ("POST", "/v1/contract/submit"),
    ("POST", "/v1/contract/ledger/append"),
    ("POST", "/v1/contract/ledger/verify"),
    ("GET", "/v1/contract/tools"),
)


def _client(monkeypatch: pytest.MonkeyPatch, profile: str) -> TestClient:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("CORTEX_PROFILE", profile)
    if profile == "full":
        monkeypatch.setenv("CORTEX_REQUIRE_AGENTIC_MARKER", "0")
    else:
        monkeypatch.delenv("CORTEX_REQUIRE_AGENTIC_MARKER", raising=False)

    # Fresh app — packaging reads CORTEX_PROFILE at call time.
    from CortexOS.api.app import create_app

    return TestClient(create_app())


@pytest.mark.parametrize("profile", ["core", "full"])
def test_shared_paths_present(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    client = _client(monkeypatch, profile)
    # Prefer OpenAPI paths — nested APIRouter routes are not always flat on app.routes.
    documented = set(client.app.openapi().get("paths") or {})
    for _method, path in SHARED_PATHS:
        assert path in documented, f"{path} missing under CORTEX_PROFILE={profile}"


def test_core_returns_501_for_agentic_and_rag(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "core")

    r_run = client.post("/run", json={"dag": {"nodes": []}})
    assert r_run.status_code == 501
    assert r_run.json()["detail"]["extra"] == "agentic"

    r_search = client.post("/search", json={"query": "hello"})
    assert r_search.status_code == 501
    assert r_search.json()["detail"]["extra"] == "rag"

    r_auto = client.post("/api/engine/auto", json={"goal": "x"})
    assert r_auto.status_code == 501
    assert r_auto.json()["detail"]["extra"] == "agentic"


def test_health_features_reports_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    core = _client(monkeypatch, "core")
    body = core.get("/health/features").json()
    assert body["extras"]["agentic"] is False
    assert body["extras"]["rag"] is False
    assert body["engine_version"]

    # full profile with marker off: first-party agentic surface available
    full = _client(monkeypatch, "full")
    body_f = full.get("/health/features").json()
    assert body_f["extras"]["agentic"] is True
    # rag depends on whether third-party modules are installed in this venv
    assert "rag" in body_f["extras"]
    assert body_f["extras"] != body["extras"] or body_f["profile"] != body["profile"]


def test_contract_routes_operation_ids() -> None:
    from CortexOS.api.contract_routes import CONTRACT_ROUTE_IDS

    assert CONTRACT_ROUTE_IDS == {
        "ask",
        "submit",
        "ledger.append",
        "ledger.verify",
        "tool.registry",
    }

    # Keep export allowlist in lockstep without importing scripts as a package.
    export = (Path(__file__).resolve().parents[2] / "scripts" / "export_openapi.py").read_text(
        encoding="utf-8"
    )
    for oid in CONTRACT_ROUTE_IDS:
        assert f'"{oid}"' in export
