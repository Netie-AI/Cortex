"""App store + /api/apps importer tests — import → gate → draft → approve → installed."""

from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from CortexOS.execution import app_store
from CortexOS.execution.app_package import MANIFEST_NAME, PORT_RANGE


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _clean_py_app() -> bytes:
    return _zip_bytes({"main.py": "print('hi')", "requirements.txt": "fastapi\n"})


def _secret_py_app() -> bytes:
    planted = "KEY = '" + "sk-" + "a" * 30 + "'"  # assembled, never literal
    return _zip_bytes({"main.py": "print('hi')", "config.py": planted})


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    app_store.init()


def test_import_clean_zip_lands_as_draft():
    out = app_store.import_zip_bytes(_clean_py_app(), name="My App")

    assert out["ok"] is True
    app = out["app"]
    assert app["status"] == "draft"
    assert app["stack"] == "python"
    assert app["name"] == "My App"
    assert app["manifest"]["commands"]["start"] == ["python", "main.py"]


def test_import_raw_zip_needs_no_manifest():
    out = app_store.import_zip_bytes(_zip_bytes({"index.html": "<h1>hi</h1>"}))
    assert out["app"]["stack"] == "static"


def test_import_secret_zip_is_blocked_and_rescan_recovers(tmp_path):
    out = app_store.import_zip_bytes(_secret_py_app())
    app = out["app"]
    assert app["status"] == "blocked"
    assert "secrets_found" in app["reasons"]

    assert app_store.approve(app["id"])["error"].startswith("not_draft")

    (tmp_path / "apps" / "incoming" / app["id"] / "config.py").unlink()
    rescanned = app_store.rescan(app["id"])["app"]
    assert rescanned["status"] == "draft"


def test_import_invalid_zip_reports_error():
    out = app_store.import_zip_bytes(b"this is not a zip")
    assert out["ok"] is False
    assert out["error"].startswith("invalid_zip")


def test_approve_assigns_port_and_installs(tmp_path):
    app = app_store.import_zip_bytes(_clean_py_app())["app"]

    approved = app_store.approve(app["id"])["app"]

    assert approved["status"] == "approved"
    assert PORT_RANGE[0] <= approved["port"] <= PORT_RANGE[1]
    installed = tmp_path / "apps" / "installed" / app["id"]
    assert installed.is_dir()
    pinned = json.loads((installed / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert pinned["port"] == approved["port"]
    assert not (tmp_path / "apps" / "incoming" / app["id"]).exists()

    assert app_store.approve(app["id"])["error"].startswith("not_draft")


def test_reject_and_delete():
    app = app_store.import_zip_bytes(_clean_py_app())["app"]

    rejected = app_store.reject(app["id"], "not needed")["app"]
    assert rejected["status"] == "rejected"
    assert rejected["rejected_reason"] == "not needed"

    assert app_store.delete_app(app["id"]) is True
    assert app_store.get_app(app["id"]) is None


# --- route smoke --------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_api_import_approve_flow(client):
    payload = base64.b64encode(_clean_py_app()).decode()

    imported = client.post("/api/apps/import", json={"zip_base64": payload}).json()
    assert imported["app"]["status"] == "draft"
    app_id = imported["app"]["id"]

    assert client.get("/api/apps").json()["apps"][0]["id"] == app_id

    approved = client.post(f"/api/apps/{app_id}/approve").json()
    assert approved["app"]["status"] == "approved"

    assert client.post(f"/api/apps/{app_id}/approve").status_code == 409
    assert client.get("/api/apps/nope").status_code == 404


def test_api_import_rejects_bad_base64(client):
    assert (
        client.post("/api/apps/import", json={"zip_base64": "@@not-base64@@"}).status_code
        == 400
    )
