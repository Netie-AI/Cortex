"""O5 — AirGPT sidecar bridge routes through the Agent SDK.

Proves the bridge AirGPT's cortex_client calls gives agents governed DMS access:
reads never leak agent-invisible columns over HTTP, writes run the full
registry → RBAC → confirm → F8 → ledger path, and every refusal is auditable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from packs.dms.security.rate_limit import reset_limiter


@pytest.fixture(scope="module")
def warehouse_db(tmp_path_factory):
    from CortexOS.dms.warehouse_db import load_inventory_csv

    db = tmp_path_factory.mktemp("wh") / "wh.duckdb"
    load_inventory_csv(db_path=db)
    return db


@pytest.fixture
def env(tmp_path, monkeypatch, warehouse_db):
    monkeypatch.delenv("DMS_LEDGER_DSN", raising=False)
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse_db))
    monkeypatch.setenv(
        "DMS_API_KEYS",
        "viewer:sk-viewer-test;steward:sk-steward-test;admin:sk-admin-test",
    )
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("PACK", "dms")
    return {"ops_db": tmp_path / "ops.db"}


@pytest.fixture
def client(env):
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    return TestClient(create_app())


VIEWER = {"X-API-Key": "sk-viewer-test"}
STEWARD = {"X-API-Key": "sk-steward-test"}


def test_query_objects_hides_pii_columns(client):
    res = client.post(
        "/dms/sidecar/query-objects",
        json={"object_type": "suppliers", "limit": 5},
        headers=VIEWER,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] and body["count"] >= 1
    for row in body["rows"]:
        assert {"email", "phone", "contact_person"}.isdisjoint(row)
        assert "supplier_name" in row


def test_query_objects_hidden_filter_403(client):
    res = client.post(
        "/dms/sidecar/query-objects",
        json={"object_type": "suppliers", "filters": {"email": "x@y.com"}},
        headers=VIEWER,
    )
    assert res.status_code == 403
    assert res.json()["detail"]["verdict"] == "filter_hidden"


def test_query_objects_unknown_404(client):
    res = client.post(
        "/dms/sidecar/query-objects", json={"object_type": "nope"}, headers=VIEWER
    )
    assert res.status_code == 404
    assert res.json()["detail"]["verdict"] == "not_found"


def test_call_action_viewer_rbac_403_and_ledgered(client, env):
    from packs.dms.audit.ledger import list_entries

    res = client.post(
        "/dms/sidecar/call-action",
        json={"action_id": "export_pptx", "params": {"title": "T"}},
        headers=VIEWER,
    )
    assert res.status_code == 403
    assert res.json()["detail"]["verdict"] == "rbac"
    denials = list_entries(db_path=env["ops_db"], event_type="action.tool_call_denied")
    assert denials and denials[-1].payload["verdict"] == "rbac"
    assert denials[-1].actor == "api_viewer"


def test_call_action_confirm_gate_409(client):
    res = client.post(
        "/dms/sidecar/call-action",
        json={"action_id": "export_pptx", "params": {"title": "T"}},
        headers=STEWARD,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["verdict"] == "confirm_required"


def test_call_action_confirmed_executes_and_ledgers(client, env, tmp_path, monkeypatch):
    from packs.dms.audit.ledger import list_entries

    out_root = tmp_path / "outputs"
    monkeypatch.setattr("netie.execution.tool_runner.OUTPUTS", out_root)
    monkeypatch.setattr("CortexOS.execution.tool_runner.OUTPUTS", out_root)

    res = client.post(
        "/dms/sidecar/call-action",
        json={
            "action_id": "export_pptx",
            "params": {"title": "Sidecar Q3", "body": "via SDK"},
            "confirmed": True,
            "run_id": "o5run",
        },
        headers=STEWARD,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["verdict"] == "pass"
    assert (out_root / "api_steward" / "o5run" / "export.pptx").is_file()

    executed = list_entries(db_path=env["ops_db"], event_type="action.tool_call")
    assert executed[-1].actor == "api_steward"
    assert executed[-1].payload["tool"] == "export_pptx"


def test_call_action_event_kind_400(client):
    res = client.post(
        "/dms/sidecar/call-action",
        json={"action_id": "item.intake", "params": {}, "confirmed": True},
        headers=STEWARD,
    )
    assert res.status_code == 400
    assert res.json()["detail"]["verdict"] == "not_invocable"


def test_call_action_unregistered_404(client):
    res = client.post(
        "/dms/sidecar/call-action",
        json={"action_id": "rm_rf", "confirmed": True},
        headers=STEWARD,
    )
    assert res.status_code == 404
    assert res.json()["detail"]["verdict"] == "unregistered"
