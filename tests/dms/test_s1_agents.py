"""S1 — watcher agents: detect → draft → compliance → human approve → publish."""
from __future__ import annotations

import pytest


@pytest.fixture
def lake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_LAKEHOUSE_HOME", str(tmp_path / "lakehouse"))
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("PACK", "dms")
    from packs.dms.lakehouse import catalog

    catalog.reset_mode_cache()
    yield tmp_path
    catalog.reset_mode_cache()


def _seed_sensor_rows(n: int = 12) -> None:
    from packs.dms.lakehouse import tables as lt

    rows = [{"event_id": f"e{i}", "ts": f"2026-07-22T0{i % 10}:00:00Z", "v": float(i)} for i in range(n)]
    lt.write_table("bronze", "stream_sensors", rows=rows, actor="test")


def test_detector_pure_sql(lake_home):
    from packs.dms.agents import detectors

    _seed_sensor_rows(12)
    ok = detectors.evaluate({
        "type": "rowcount", "table": "bronze.stream_sensors", "op": ">", "bound": 100,
    })
    assert ok.fired is False and ok.value == 12.0

    fired = detectors.evaluate({
        "type": "rowcount", "table": "bronze.stream_sensors", "op": ">", "bound": 10,
    })
    assert fired.fired is True and fired.value == 12.0
    assert "FIRED" in fired.detail

    thr = detectors.evaluate({
        "type": "threshold", "table": "bronze.stream_sensors",
        "agg": "max", "column": "v", "op": ">=", "bound": 11,
    })
    assert thr.fired is True and thr.value == 11.0

    with pytest.raises(detectors.DetectorError):
        detectors.evaluate({"type": "rowcount", "table": "evil;drop", "op": ">", "bound": 0})


def test_approval_gate_blocks_publish(lake_home):
    from packs.dms.agents import employee, registry

    _seed_sensor_rows(15)
    registry.create_agent(
        "watcher-a",
        name="Sensor Watcher",
        created_by="steward",
        detector_cfg={
            "type": "rowcount", "table": "bronze.stream_sensors", "op": ">", "bound": 5,
        },
    )
    result = employee.run_agent("watcher-a", actor="steward")
    assert result["status"] == "pending_approval"
    assert result["verdict"]["requires_human"] is True
    run_id = result["run_id"]

    # no artifact until approve
    run = registry.get_run(run_id)
    assert run["artifact_path"] is None
    assert run["status"] == "pending_approval"

    rejected = employee.reject_run(run_id, approver="steward", reason="noise")
    assert rejected["status"] == "rejected"
    with pytest.raises(PermissionError):
        employee.approve_run(run_id, approver="steward")


def test_approve_publishes_artifact(lake_home, monkeypatch):
    from packs.dms.agents import employee, registry

    out = lake_home / "outputs"
    monkeypatch.setattr(employee, "OUTPUTS", out)

    _seed_sensor_rows(8)
    registry.create_agent(
        "watcher-b",
        created_by="steward",
        detector_cfg={
            "type": "rowcount", "table": "bronze.stream_sensors", "op": ">=", "bound": 8,
        },
    )
    run = employee.run_agent("watcher-b", actor="steward")
    assert run["status"] == "pending_approval"
    published = employee.approve_run(run["run_id"], approver="steward")
    assert published["status"] == "approved"
    artifact = out / "steward" / run["run_id"] / "report.md"
    assert artifact.is_file()
    text = artifact.read_text(encoding="utf-8")
    assert "Sensor" in text or "watcher-b" in text or "alert" in text.lower()
    assert "Requires human approval" in text or "requires human" in text.lower()


def test_no_trigger_when_below_bound(lake_home):
    from packs.dms.agents import employee, registry

    _seed_sensor_rows(3)
    registry.create_agent(
        "quiet",
        created_by="steward",
        detector_cfg={
            "type": "rowcount", "table": "bronze.stream_sensors", "op": ">", "bound": 50,
        },
    )
    result = employee.run_agent("quiet", actor="steward")
    assert result["status"] == "no_trigger"
    assert result["detection"]["fired"] is False


def test_ledger_chain_complete(lake_home, monkeypatch):
    from packs.dms.agents import employee, registry
    from packs.dms.audit import ledger

    out = lake_home / "outputs"
    monkeypatch.setattr(employee, "OUTPUTS", out)
    _seed_sensor_rows(10)
    registry.create_agent(
        "ledger-agent",
        created_by="steward",
        detector_cfg={
            "type": "rowcount", "table": "bronze.stream_sensors", "op": ">", "bound": 1,
        },
    )
    run = employee.run_agent("ledger-agent", actor="steward")
    employee.approve_run(run["run_id"], approver="steward")

    entries = ledger.list_entries()
    types = {e.event_type for e in entries}
    assert "agent.checked" in types
    assert "agent.published" in types
    verify = ledger.verify()
    assert verify.ok is True


def test_agent_api_rbac(lake_home):
    try:
        from fastapi.testclient import TestClient
    except Exception:  # pragma: no cover
        pytest.skip("fastapi testclient unavailable")
    from CortexOS.api.app import create_app
    from packs.dms.lakehouse import tables as lt

    rows = [{"event_id": f"e{i}", "ts": "2026-07-22T00:00:00Z", "v": 1.0} for i in range(6)]
    lt.write_table("bronze", "stream_api", rows=rows, actor="test")

    client = TestClient(create_app())
    body = {
        "agent_id": "api-watcher",
        "name": "API Watcher",
        "detector_cfg": {
            "type": "rowcount", "table": "bronze.stream_api", "op": ">", "bound": 2,
        },
    }
    assert client.post("/dms/agents", json=body,
                       headers={"X-API-Key": "dms-demo-viewer-key"}).status_code == 403
    created = client.post("/dms/agents", json=body,
                          headers={"X-API-Key": "dms-demo-steward-key"})
    assert created.status_code == 200

    listed = client.get("/dms/agents", headers={"X-API-Key": "dms-demo-viewer-key"})
    assert listed.status_code == 200
    assert any(a["agent_id"] == "api-watcher" for a in listed.json()["agents"])

    ran = client.post("/dms/agents/api-watcher/run",
                      headers={"X-API-Key": "dms-demo-steward-key"})
    assert ran.status_code == 200
    assert ran.json()["status"] == "pending_approval"
    run_id = ran.json()["run_id"]

    approved = client.post(f"/dms/agents/runs/{run_id}/approve",
                           headers={"X-API-Key": "dms-demo-steward-key"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


@pytest.mark.skip(reason="S1 slice: DBOS durable resume not landed yet (see BUILD_PLAN S1 anti-scope note in employee.py)")
def test_workflow_resume_after_kill():
    assert False, "placeholder for DBOS resume chaos-lite"


@pytest.mark.skip(reason="S1 slice: @agent chat dispatch not landed yet")
def test_agent_chat_dispatch():
    assert False, "placeholder for F2 @agent dispatch"
