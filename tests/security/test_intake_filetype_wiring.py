"""C-SEC-4 proof tests — filetype_guard wired in front of photo + ingest.

Deny paths proven (fail-closed):
  D1  exe bytes named report.csv → ingest API 415, nothing written to the drop dir
  D2  same file via the folder-scan loader → quarantined `failed`, no bronze table
  D3  plain text named .xlsx (container spoof) → denied
  D4  exe bytes as a photo → intake ValueError + estimate-dims 415
Allow paths proven: real CSV loads; real PNG passes the photo policy.
"""
from __future__ import annotations

import base64

import pytest

MZ_EXE = b"MZ\x90\x00\x03\x00\x00\x00PE\x00\x00fakebinarypayload"
# Smallest valid-enough PNG signature + IHDR fragment for sniffing purposes.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 17


@pytest.fixture
def lake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_LAKEHOUSE_HOME", str(tmp_path / "lakehouse"))
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    from packs.dms.lakehouse import catalog

    catalog.reset_mode_cache()
    yield tmp_path
    catalog.reset_mode_cache()


def test_policy_unit_matrix():
    from packs.dms.security.intake_policy import check_photo, check_upload

    assert check_upload(b"id,name\n1,a\n", "csv").ok
    assert check_upload(b'{"a":1}\n', "jsonl").ok
    assert not check_upload(MZ_EXE, "csv").ok                    # D1 core
    assert "executable" in (check_upload(MZ_EXE, "csv").reason or "")
    assert not check_upload(b"just,text\n", "xlsx").ok           # D3
    assert not check_upload(b"", "csv").ok                       # empty fail-closed
    assert not check_upload(b"id\n1\n", "exe").ok                # unknown ext denied
    assert check_photo(PNG_BYTES).ok
    assert not check_photo(MZ_EXE).ok
    assert "executable" in (check_photo(MZ_EXE).reason or "")


def test_ingest_api_denies_spoofed_exe_before_write(lake_home, monkeypatch):
    monkeypatch.setenv("PACK", "dms")
    drop = lake_home / "drop_api"
    monkeypatch.setenv("DMS_INGEST_DIR", str(drop))
    try:
        from fastapi.testclient import TestClient
    except Exception:  # pragma: no cover
        pytest.skip("fastapi testclient unavailable")
    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    body = {"filename": "report.csv", "content_b64": base64.b64encode(MZ_EXE).decode()}
    r = client.post("/dms/ingest/file", json=body,
                    headers={"X-API-Key": "dms-demo-steward-key"})
    assert r.status_code == 415
    assert not (drop / "report.csv").exists()                    # D1: never touched disk

    ok_body = {"filename": "clean.csv",
               "content_b64": base64.b64encode(b"id,v\n1,a\n").decode()}
    ok = client.post("/dms/ingest/file", json=ok_body,
                     headers={"X-API-Key": "dms-demo-steward-key"})
    assert ok.status_code == 200 and ok.json()["status"] == "loaded"


def test_loader_quarantines_spoofed_exe(lake_home):
    from packs.dms.ingest import loader
    from packs.dms.lakehouse import tables as lt

    bad = lake_home / "evilreport.csv"
    bad.write_bytes(MZ_EXE)
    result = loader.load_one(bad)
    assert result.status == "failed" and "filetype_guard" in result.error   # D2
    assert "evilreport_raw" not in lt.list_tables()["bronze"]
    assert any("filetype_guard" in (e.get("error") or "")
               for e in loader.ledger_entries())


def test_photo_intake_rejects_exe(lake_home, monkeypatch):
    monkeypatch.setenv("PACK", "dms")
    try:
        from fastapi.testclient import TestClient
    except Exception:  # pragma: no cover
        pytest.skip("fastapi testclient unavailable")
    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    r = client.post("/dms/items/estimate-dims",
                    json={"photo": base64.b64encode(MZ_EXE).decode()})
    assert r.status_code == 415                                  # D4 route half
    assert "rejected" in str(r.json().get("detail", ""))


def test_vision_intake_function_rejects_exe(lake_home):
    from packs.dms.vision.intake import intake_item
    from packs.dms.vision.warehouse_store import create_location

    loc = create_location(kind="zone", code="WH-SEC-T")
    with pytest.raises(ValueError, match="photo rejected"):
        intake_item(sku="SKU-T1", label="t", location_code=loc.code,
                    photo_b64=base64.b64encode(MZ_EXE).decode())
