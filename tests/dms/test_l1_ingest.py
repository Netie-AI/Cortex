"""L1 — exactly-once file ingest into the bronze lakehouse layer."""
from __future__ import annotations

import pytest


@pytest.fixture
def lake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_LAKEHOUSE_HOME", str(tmp_path / "lakehouse"))
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    from packs.dms.lakehouse import catalog

    catalog.reset_mode_cache()
    yield tmp_path
    catalog.reset_mode_cache()


def _write(p, text):
    p.write_text(text, encoding="utf-8")
    return p


def test_exactly_once(lake_home):
    from packs.dms.ingest import loader

    f = _write(lake_home / "widgets.csv", "id,name\n1,a\n2,b\n")
    r1 = loader.load_one(f)
    r2 = loader.load_one(f)
    assert r1.status == "loaded" and r1.rows == 2 and r1.table == "widgets_raw"
    assert r2.status == "skipped_duplicate"
    statuses = [(e["filename"], e["status"]) for e in loader.ledger_entries()]
    assert ("widgets.csv", "loaded") in statuses
    assert ("widgets.csv", "skipped_duplicate") in statuses


def test_corrupt_quarantined_no_partial(lake_home):
    from packs.dms.ingest import loader
    from packs.dms.lakehouse import tables as lt

    bad = _write(lake_home / "broken.json", "{not valid json ::::")
    r = loader.load_one(bad)
    assert r.status == "failed" and r.error
    # no partial table left behind
    assert "broken_raw" not in lt.list_tables()["bronze"]
    assert any(e["status"] == "failed" for e in loader.ledger_entries())


def test_meta_columns_and_permissive(lake_home):
    from packs.dms.ingest import loader
    from packs.dms.lakehouse import tables as lt

    f = _write(lake_home / "mix.csv", "a,b\n1,x\n2,y\n")
    loader.load_one(f)
    row = lt.read("bronze", "mix_raw", limit=1)[0]
    for meta in ("_source_file", "_content_hash", "_loaded_at", "_rescued"):
        assert meta in row
    assert isinstance(row["a"], str)  # permissive: all-varchar bronze


def test_jsonl_and_unsupported(lake_home):
    from packs.dms.ingest import loader

    jl = _write(lake_home / "ev.jsonl", '{"a":1}\n{"a":2}\n')
    assert loader.load_one(jl).status == "loaded"
    txt = _write(lake_home / "note.txt", "hello")
    assert loader.load_one(txt).status == "unsupported"


def test_scan_skips_loaded(lake_home):
    from packs.dms.ingest import loader

    d = lake_home / "drop"
    d.mkdir()
    _write(d / "one.csv", "x\n1\n")
    _write(d / "two.csv", "y\n2\n")
    loader.load_one(d / "one.csv")
    new = [p.name for p in loader.scan(d)]
    assert "two.csv" in new and "one.csv" not in new


def test_upload_api_rbac_and_ledger(lake_home, monkeypatch):
    monkeypatch.setenv("DMS_INGEST_DIR", str(lake_home / "drop_api"))
    monkeypatch.setenv("PACK", "dms")
    try:
        from fastapi.testclient import TestClient
    except Exception:  # pragma: no cover
        pytest.skip("fastapi testclient unavailable")
    import base64

    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    payload = base64.b64encode(b"id,v\n1,a\n2,b\n").decode()
    body = {"filename": "up.csv", "content_b64": payload}

    # viewer forbidden, steward allowed
    assert client.post("/dms/ingest/file", json=body,
                       headers={"X-API-Key": "dms-demo-viewer-key"}).status_code == 403
    r = client.post("/dms/ingest/file", json=body,
                    headers={"X-API-Key": "dms-demo-steward-key"})
    assert r.status_code == 200 and r.json()["status"] == "loaded"

    led = client.get("/dms/ingest/ledger", headers={"X-API-Key": "dms-demo-viewer-key"})
    assert led.status_code == 200
    assert any(e["filename"] == "up.csv" for e in led.json()["entries"])


def test_upload_rejects_path_traversal(lake_home, monkeypatch):
    monkeypatch.setenv("DMS_INGEST_DIR", str(lake_home / "drop_api2"))
    monkeypatch.setenv("PACK", "dms")
    try:
        from fastapi.testclient import TestClient
    except Exception:  # pragma: no cover
        pytest.skip("fastapi testclient unavailable")
    import base64

    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    body = {"filename": "../../evil.csv", "content_b64": base64.b64encode(b"x\n1\n").decode()}
    r = client.post("/dms/ingest/file", json=body, headers={"X-API-Key": "dms-demo-steward-key"})
    # sanitized to a safe name and ingested inside the drop dir, not written up a level
    assert r.status_code == 200
    assert "evil" in (r.json()["table"] or "")
