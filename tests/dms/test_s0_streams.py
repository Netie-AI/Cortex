"""S0 — streaming webhook intake into the bronze lakehouse."""
from __future__ import annotations

import pytest


@pytest.fixture
def lake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_LAKEHOUSE_HOME", str(tmp_path / "lakehouse"))
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("DMS_STREAM_BATCH", "5")
    monkeypatch.setenv("DMS_STREAM_HARD_CAP", "20")
    from packs.dms.lakehouse import catalog
    from packs.dms.streams import buffer

    catalog.reset_mode_cache()
    buffer._BUFFERS.clear()
    buffer.reset_writer()
    yield tmp_path
    buffer._BUFFERS.clear()
    buffer.reset_writer()
    catalog.reset_mode_cache()


def test_register_and_list(lake_home):
    from packs.dms.streams import registry

    registry.create_stream("sensors_a", name="Sensor A", created_by="steward")
    registry.create_stream("sensors_a", created_by="steward")  # idempotent
    ids = [s["stream_id"] for s in registry.list_streams()]
    assert ids.count("sensors_a") == 1
    assert not registry.valid_stream_id("BAD ID!")


def test_append_autoflush_and_read(lake_home):
    from packs.dms.streams import buffer
    from packs.dms.lakehouse import tables as lt

    r1 = buffer.append_events("s1", [{"event_id": "e1"}, {"event_id": "e2"}])
    assert r1 == {"accepted": 2, "buffered": 2, "flushed": 0}
    r2 = buffer.append_events("s1", [{"event_id": f"e{i}"} for i in range(3, 7)])
    assert r2["flushed"] >= 5  # crossed the batch threshold of 5
    rows = lt.read("bronze", "stream_s1")
    assert len(rows) >= 5
    assert set(rows[0].keys()) >= {"event_id", "ts", "payload", "_stream_id", "_received_at"}


def test_dedup_within_batch(lake_home):
    from packs.dms.streams import buffer
    from packs.dms.lakehouse import tables as lt

    buffer.append_events("s2", [{"event_id": "dup"}, {"event_id": "dup"}, {"event_id": "x"}],
                         auto_flush=False)
    assert buffer.flush("s2") == 2  # dup collapsed
    ids = sorted(r["event_id"] for r in lt.read("bronze", "stream_s2"))
    assert ids == ["dup", "x"]


def test_backpressure(lake_home):
    from packs.dms.streams import buffer

    with pytest.raises(buffer.BackpressureError):
        buffer.append_events("s3", [{"i": i} for i in range(50)], auto_flush=False)  # > hard cap 20


def test_events_api_rbac_and_backpressure(lake_home, monkeypatch):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_STREAM_BATCH", "100000")  # never auto-flush
    monkeypatch.setenv("DMS_STREAM_HARD_CAP", "10")
    try:
        from fastapi.testclient import TestClient
    except Exception:  # pragma: no cover
        pytest.skip("fastapi testclient unavailable")
    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    body = {"events": [{"event_id": "a"}, {"event_id": "b"}]}

    # viewer forbidden, steward ok
    assert client.post("/dms/streams/apis/events", json=body,
                       headers={"X-API-Key": "dms-demo-viewer-key"}).status_code == 403
    ok = client.post("/dms/streams/apis/events", json=body,
                     headers={"X-API-Key": "dms-demo-steward-key"})
    assert ok.status_code == 200 and ok.json()["accepted"] == 2

    # exceed hard cap without flushing → 429
    big = {"events": [{"i": i} for i in range(50)]}
    r = client.post("/dms/streams/apis/events", json=big,
                    headers={"X-API-Key": "dms-demo-steward-key"})
    assert r.status_code == 429

    # stream auto-registered on first use
    listed = client.get("/dms/streams", headers={"X-API-Key": "dms-demo-viewer-key"})
    assert any(s["stream_id"] == "apis" for s in listed.json()["streams"])


def test_simulator_inprocess(lake_home):
    from scripts.stream_simulate import run_inprocess
    from packs.dms.lakehouse import tables as lt

    result = run_inprocess("simstream", rate=10, seconds=1)
    assert result["sent"] >= 10
    rows = lt.read("bronze", "stream_simstream")
    assert len(rows) >= 10
