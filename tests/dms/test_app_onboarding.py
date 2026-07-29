"""Folder import, approval-screen copy, and auto-dockerize.

The promise: point at a project folder, read one plain sentence about what it
is, and get a Dockerfile written for you if it needs one.
"""

from __future__ import annotations

import pytest

from CortexOS.execution import app_package, app_store


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    app_store.init()


def _python_project(tmp_path):
    root = tmp_path / "my-project"
    root.mkdir()
    (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return root


# --- folder import -----------------------------------------------------------


def test_folder_import_needs_no_zip_or_base64(tmp_path):
    out = app_store.import_folder(_python_project(tmp_path))

    assert out["ok"] is True
    assert out["app"]["name"] == "my-project"
    assert out["app"]["stack"] == "python"
    assert out["app"]["status"] == "draft"


def test_folder_import_rejects_nonsense_paths(tmp_path):
    assert app_store.import_folder(tmp_path / "nope")["error"] == "missing_folder"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert app_store.import_folder(empty)["error"] == "empty_folder"


def test_folder_import_refuses_a_whole_drive(tmp_path, monkeypatch):
    monkeypatch.setattr(app_store, "MAX_FOLDER_FILES", 2)
    root = tmp_path / "huge"
    root.mkdir()
    for i in range(3):
        (root / f"f{i}.py").write_text("x", encoding="utf-8")

    assert app_store.import_folder(root)["error"].startswith("folder_too_many_files")


# --- approval-screen copy ----------------------------------------------------


def test_describe_reads_like_a_sentence_not_a_manifest(tmp_path):
    manifest = app_package.generate_manifest(_python_project(tmp_path))

    about = app_package.describe(manifest)

    assert about["summary"] == "This is a Python app with 2 files."
    assert about["safe_to_approve"] is True
    assert any("own port" in line for line in about["will_do"])
    assert "{" not in about["summary"]  # no JSON leaking into user-facing copy


def test_describe_warns_about_secrets_and_names_the_file(tmp_path):
    root = _python_project(tmp_path)
    manifest = app_package.generate_manifest(root)
    findings = [{"file": "config.py", "line": 1, "pattern": "openai_key"}]

    about = app_package.describe(manifest, findings)

    assert about["safe_to_approve"] is False
    assert "config.py" in about["watch_out"][0]


def test_describe_flags_an_app_we_cannot_start(tmp_path):
    unknown = tmp_path / "mystery"
    unknown.mkdir()
    (unknown / "notes.txt").write_text("hello", encoding="utf-8")

    about = app_package.describe(app_package.generate_manifest(unknown))

    assert about["safe_to_approve"] is False
    assert any("can't run" in w for w in about["watch_out"])


# --- auto-dockerize ----------------------------------------------------------


def test_dockerfile_written_for_a_python_app(tmp_path):
    root = _python_project(tmp_path)

    out = app_package.ensure_dockerfile(root)

    assert out["created"] is True
    content = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12-slim" in content
    assert "pip install --no-cache-dir -r requirements.txt" in content
    assert "python main.py" in content
    assert "EXPOSE 8080" in content


def test_dockerfile_written_for_node_and_static(tmp_path):
    node = tmp_path / "node-app"
    node.mkdir()
    (node / "package.json").write_text('{"name":"n","scripts":{"start":"node i.js"}}', encoding="utf-8")
    (node / "package-lock.json").write_text("{}", encoding="utf-8")
    assert app_package.ensure_dockerfile(node)["created"] is True
    assert "npm ci --omit=dev" in (node / "Dockerfile").read_text(encoding="utf-8")

    static = tmp_path / "site"
    static.mkdir()
    (static / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    assert app_package.ensure_dockerfile(static)["created"] is True
    assert "http.server" in (static / "Dockerfile").read_text(encoding="utf-8")


def test_never_overwrites_an_authors_own_dockerfile(tmp_path):
    root = _python_project(tmp_path)
    (root / "Dockerfile").write_text("FROM scratch\n# mine\n", encoding="utf-8")

    out = app_package.ensure_dockerfile(root)

    assert out["created"] is False
    assert out["reason"] == "already_has_dockerfile"
    assert "# mine" in (root / "Dockerfile").read_text(encoding="utf-8")


def test_dockerfile_generation_is_deterministic(tmp_path):
    root = _python_project(tmp_path)
    app_package.ensure_dockerfile(root)
    first = (root / "Dockerfile").read_text(encoding="utf-8")
    (root / "Dockerfile").unlink()
    app_package.ensure_dockerfile(root)

    assert (root / "Dockerfile").read_text(encoding="utf-8") == first


def test_dockerize_through_the_store(tmp_path):
    imported = app_store.import_folder(_python_project(tmp_path))
    app_id = imported["app"]["id"]

    out = app_store.dockerize(app_id)

    assert out["ok"] is True and out["created"] is True

    again = app_store.dockerize(app_id)
    assert again["created"] is False  # idempotent


def test_dockerize_unknown_app_is_handled():
    assert app_store.dockerize("nope")["error"] == "unknown_app"


# --- routes ------------------------------------------------------------------


def test_routes_expose_folder_import_dockerize_and_about(tmp_path, monkeypatch):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    from fastapi.testclient import TestClient

    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    project = _python_project(tmp_path)

    imported = client.post(
        "/api/apps/import-folder", json={"path": str(project)}
    ).json()
    assert imported["ok"] is True
    assert imported["app"]["about"]["summary"].startswith("This is a Python app")

    app_id = imported["app"]["id"]
    docked = client.post(f"/api/apps/{app_id}/dockerize").json()
    assert docked["created"] is True

    bad = client.post("/api/apps/import-folder", json={"path": str(tmp_path / "ghost")})
    assert bad.status_code == 400
    assert bad.json()["detail"]["title"] == "We couldn't find that folder"
