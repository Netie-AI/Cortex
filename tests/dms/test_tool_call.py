"""F8 tool-call vertical slice — allowlist, RBAC, ledger, path sandbox."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from packs.dms.security.rate_limit import reset_limiter


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    db = tmp_path / "ledger.db"
    monkeypatch.delenv("DMS_LEDGER_DSN", raising=False)
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    return db


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


def test_allowlist_deny(ledger_db, tmp_path, monkeypatch):
    from netie.execution.tool_runner import ToolCallError, run_tool_call
    from packs.dms.audit.ledger import list_entries

    monkeypatch.setattr("netie.execution.tool_runner.OUTPUTS", tmp_path / "outputs")
    with pytest.raises(ToolCallError) as exc:
        run_tool_call("rm_rf", {"title": "x"}, actor="steward", run_id="run1", db_path=ledger_db)
    assert exc.value.verdict == "allowlist"

    events = list_entries(db_path=ledger_db, event_type="action.tool_call_denied")
    assert len(events) == 1
    assert events[0].payload["tool"] == "rm_rf"
    assert events[0].payload["verdict"] == "allowlist"
    assert not list(tmp_path.rglob("export.pptx"))


def test_viewer_403(dms_client, api_keys_env, ledger_db):
    res = dms_client.post(
        "/dms/actions/export_pptx",
        json={"params": {"title": "Demo"}, "run_id": "rview"},
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 403


def test_steward_success_file_and_ledger(dms_client, api_keys_env, ledger_db, tmp_path, monkeypatch):
    from packs.dms.audit.ledger import list_entries

    out_root = tmp_path / "outputs"
    monkeypatch.setattr("netie.execution.tool_runner.OUTPUTS", out_root)
    # Also patch module used if imported as CortexOS path
    monkeypatch.setattr("CortexOS.execution.tool_runner.OUTPUTS", out_root)

    res = dms_client.post(
        "/dms/actions/export_pptx",
        json={"params": {"title": "Q2 Warehouse", "body": "demo"}, "run_id": "runabc"},
        headers={"X-API-Key": api_keys_env["steward"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["verdict"] == "pass"
    assert body["tool"] == "export_pptx"

    pptx = out_root / "api_steward" / "runabc" / "export.pptx"
    assert pptx.is_file()
    assert pptx.stat().st_size > 100

    events = list_entries(db_path=ledger_db, event_type="action.tool_call")
    assert len(events) >= 1
    last = events[-1]
    assert last.actor == "api_steward"
    assert last.payload["tool"] == "export_pptx"
    assert last.payload["verdict"] == "pass"
    assert "params_hash" in last.payload
    assert "export.pptx" in last.payload["path"]


def test_path_escape_denied(ledger_db, tmp_path, monkeypatch):
    from netie.execution.tool_runner import ToolCallError, run_tool_call
    from packs.dms.audit.ledger import list_entries

    monkeypatch.setattr("netie.execution.tool_runner.OUTPUTS", tmp_path / "outputs")
    with pytest.raises(ToolCallError) as exc:
        run_tool_call(
            "export_pptx",
            {"title": "Nope"},
            actor="../etc",
            run_id="passwd",
            db_path=ledger_db,
        )
    assert exc.value.verdict == "path_escape"

    events = list_entries(db_path=ledger_db, event_type="action.tool_call_denied")
    assert any(e.payload.get("verdict") == "path_escape" for e in events)
    assert not list((tmp_path / "outputs").rglob("export.pptx")) if (tmp_path / "outputs").exists() else True


def test_compliance_empty_title_denied(ledger_db, tmp_path, monkeypatch):
    from netie.execution.tool_runner import ToolCallError, run_tool_call

    monkeypatch.setattr("netie.execution.tool_runner.OUTPUTS", tmp_path / "outputs")
    with pytest.raises(ToolCallError) as exc:
        run_tool_call(
            "export_pptx",
            {"title": ""},
            actor="steward",
            run_id="emptytitle",
            db_path=ledger_db,
        )
    assert exc.value.verdict == "compliance_fail"
