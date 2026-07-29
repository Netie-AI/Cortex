"""App package + ship gate tests — detect, deterministic manifest, scan, pack, gate."""

from __future__ import annotations

import json
import zipfile

import pytest

from CortexOS.execution import app_package as ap


def _node_app(tmp_path):
    root = tmp_path / "node-app"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"name": "demo", "main": "index.js", "scripts": {"start": "node index.js"}}),
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text("{}", encoding="utf-8")
    (root / "index.js").write_text("console.log('hi')", encoding="utf-8")
    return root


def _python_app(tmp_path):
    root = tmp_path / "py-app"
    root.mkdir()
    (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (root / "main.py").write_text("print('hi')", encoding="utf-8")
    return root


def test_detect_stacks(tmp_path):
    assert ap.detect_stack(_node_app(tmp_path)) == "node"
    assert ap.detect_stack(_python_app(tmp_path)) == "python"

    docker = tmp_path / "docker-app"
    docker.mkdir()
    (docker / "Dockerfile").write_text("FROM alpine", encoding="utf-8")
    assert ap.detect_stack(docker) == "docker"

    static = tmp_path / "static-app"
    static.mkdir()
    (static / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    assert ap.detect_stack(static) == "static"

    empty = tmp_path / "empty-app"
    empty.mkdir()
    assert ap.detect_stack(empty) == "unknown"


def test_manifest_is_deterministic_and_pinned(tmp_path):
    root = _python_app(tmp_path)

    first = ap.generate_manifest(root)
    second = ap.generate_manifest(root)

    assert first == second
    assert first["commands"]["build"] == ["pip", "install", "-r", "requirements.txt"]
    assert first["commands"]["start"] == ["python", "main.py"]
    assert first["api_base"] == "http://127.0.0.1:8765"
    assert len(first["content_sha256"]) == 64


def test_node_manifest_uses_lockfile_and_start_script(tmp_path):
    manifest = ap.generate_manifest(_node_app(tmp_path))

    assert manifest["commands"]["build"] == ["npm", "ci"]
    assert manifest["commands"]["start"] == ["npm", "start"]


def test_scan_secrets_finds_planted_key_and_passes_clean(tmp_path):
    root = _python_app(tmp_path)
    assert ap.scan_secrets(root) == []

    planted = "OPENAI_API_KEY = '" + "sk-" + "a" * 30 + "'"  # assembled, never literal
    (root / "config.py").write_text(planted, encoding="utf-8")

    findings = ap.scan_secrets(root)
    assert findings
    assert findings[0]["file"] == "config.py"


def test_pack_unpack_roundtrip_verifies(tmp_path):
    root = _python_app(tmp_path)

    packed = ap.pack(root, tmp_path / "out.zip")
    assert len(packed["sha256"]) == 64

    result = ap.unpack(packed["zip"], tmp_path / "restored")

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["manifest"]["stack"] == "python"
    assert result["unsafe_members"] == []


def test_unpack_blocks_zip_slip(tmp_path):
    evil_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr(ap.MANIFEST_NAME, "{}")
        zf.writestr("../evil.txt", "pwned")

    result = ap.unpack(evil_zip, tmp_path / "jail")

    assert "../evil.txt" in result["unsafe_members"]
    assert not (tmp_path / "evil.txt").exists()


def test_ship_gate_clean_app_is_draft_awaiting_human(tmp_path):
    report = ap.ship_gate(_python_app(tmp_path))

    assert report["status"] == "draft"
    assert report["next"] == "human_approval"
    assert report["reasons"] == []


def test_ship_gate_blocks_secrets(tmp_path):
    root = _python_app(tmp_path)
    (root / "config.py").write_text(
        "TOKEN = '" + "ghp_" + "b" * 36 + "'", encoding="utf-8"
    )

    report = ap.ship_gate(root)

    assert report["status"] == "blocked"
    assert "secrets_found" in report["reasons"]


def test_ship_gate_blocks_unknown_stack_and_custom_stress(tmp_path):
    empty = tmp_path / "empty-app"
    empty.mkdir()
    assert ap.ship_gate(empty)["status"] == "blocked"

    root = _python_app(tmp_path)
    report = ap.ship_gate(root, stress=lambda base, manifest: False)
    assert report["status"] == "blocked"
    assert "stress_failed" in report["reasons"]


def test_assign_port_avoids_airgpt_api():
    port = ap.assign_port()
    assert port != 8765
    assert ap.PORT_RANGE[0] <= port <= ap.PORT_RANGE[1]

    preferred = ap.assign_port(preferred=8765)
    assert preferred != 8765
