"""IMAGE-AUTH-01: auth-off is refused outside dev mode, and images cannot set it.

(a) ``DMS_AUTH_DISABLED`` without ``CORTEX_DEV_MODE`` raises
``AUTH_DISABLED_WITHOUT_DEV_MODE`` before FastAPI exists, so no route serves.
(b) A fixture Dockerfile ``ENV`` or a fixture compose ``environment`` / ``env_file``
makes ``scripts/check_supply_chain.py`` exit 1 with ``AUTH_DISABLED_IN_IMAGE``.
The same three channels exit 1 with ``DEV_MODE_IN_IMAGE`` for ``CORTEX_DEV_MODE``.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

import pytest

from scripts import check_supply_chain as sc

ROOT = Path(__file__).resolve().parents[2]
_DEV_FILE_REF = re.compile(r"docker-compose\.dev\.yml|(?<![\w.-])dev\.env(?![\w.-])")


def _git(tmp_path: Path, *args: str) -> None:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    subprocess.run(
        ["git", *args],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=env,
    )


@pytest.mark.parametrize("flag", ["1", "true", "yes", "TRUE"])
def test_auth_disabled_without_dev_mode_refuses(monkeypatch, caplog, flag: str) -> None:
    monkeypatch.setenv("DMS_AUTH_DISABLED", flag)
    monkeypatch.delenv("CORTEX_DEV_MODE", raising=False)

    import fastapi

    from CortexOS.api.app import create_app
    from CortexOS.api.startup_auth import AuthDisabledWithoutDevMode

    constructed: list[bool] = []
    real = fastapi.FastAPI

    def _spy(*args, **kwargs):
        constructed.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(fastapi, "FastAPI", _spy)
    with caplog.at_level(logging.ERROR, logger="cortex.startup"):
        with pytest.raises(AuthDisabledWithoutDevMode, match="AUTH_DISABLED_WITHOUT_DEV_MODE"):
            create_app()
    assert constructed == []
    assert "AUTH_DISABLED_WITHOUT_DEV_MODE" in caplog.text


def test_dev_mode_keeps_auth_off_and_serves(monkeypatch) -> None:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("CORTEX_DEV_MODE", "1")
    from fastapi.testclient import TestClient

    from CortexOS.api.app import create_app
    from packs.dms.security.api_auth import auth_required

    assert auth_required() is False
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_shipped_images_do_not_set_auth_disabled() -> None:
    for name in ("Dockerfile", "Dockerfile.constructor", "Dockerfile.core", "Dockerfile.full"):
        text = (ROOT / name).read_text(encoding="utf-8")
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        assert "DMS_AUTH_DISABLED" not in code, name
        assert "CORTEX_DEV_MODE" not in code, name
    compose = (ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")
    code = "\n".join(line for line in compose.splitlines() if not line.lstrip().startswith("#"))
    assert "DMS_AUTH_DISABLED" not in code
    assert "CORTEX_DEV_MODE" not in code
    assert "dev.env.example" not in code


def test_fixture_dockerfile_env_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "Dockerfile").write_text("FROM scratch\nENV DMS_AUTH_DISABLED=1\n", encoding="utf-8")
    assert sc.main(tmp_path) == 1
    out = capsys.readouterr().out
    assert "AUTH_DISABLED_IN_IMAGE" in out
    assert "via ENV" in out


def test_fixture_compose_environment_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  api:\n    image: scratch\n    environment:\n      DMS_AUTH_DISABLED: \"1\"\n",
        encoding="utf-8",
    )
    assert sc.main(tmp_path) == 1
    out = capsys.readouterr().out
    assert "AUTH_DISABLED_IN_IMAGE" in out
    assert "via environment" in out


def test_fixture_compose_env_file_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "local.env").write_text("DMS_AUTH_DISABLED=1\n", encoding="utf-8")
    (tmp_path / "compose.yaml").write_text(
        "services:\n  api:\n    image: scratch\n    env_file:\n      - local.env\n",
        encoding="utf-8",
    )
    assert sc.main(tmp_path) == 1
    out = capsys.readouterr().out
    assert "AUTH_DISABLED_IN_IMAGE" in out
    assert "via env_file" in out


def test_fixture_dockerfile_env_dev_mode_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "Dockerfile").write_text("FROM scratch\nENV CORTEX_DEV_MODE=1\n", encoding="utf-8")
    assert sc.main(tmp_path) == 1
    out = capsys.readouterr().out
    assert "DEV_MODE_IN_IMAGE" in out
    assert "via ENV" in out


def test_fixture_compose_environment_dev_mode_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  api:\n    image: scratch\n    environment:\n      CORTEX_DEV_MODE: \"1\"\n",
        encoding="utf-8",
    )
    assert sc.main(tmp_path) == 1
    out = capsys.readouterr().out
    assert "DEV_MODE_IN_IMAGE" in out
    assert "via environment" in out


def test_fixture_compose_env_file_dev_mode_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "local.env").write_text("CORTEX_DEV_MODE=1\n", encoding="utf-8")
    (tmp_path / "compose.yaml").write_text(
        "services:\n  api:\n    image: scratch\n    env_file:\n      - local.env\n",
        encoding="utf-8",
    )
    assert sc.main(tmp_path) == 1
    out = capsys.readouterr().out
    assert "DEV_MODE_IN_IMAGE" in out
    assert "via env_file" in out


def test_commented_flag_is_not_a_set(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "Dockerfile").write_text(
        "# ENV DMS_AUTH_DISABLED=1\n# ENV CORTEX_DEV_MODE=1\nFROM scratch\n",
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.yml").write_text(
        "# DMS_AUTH_DISABLED=1\n# CORTEX_DEV_MODE=1\nservices:\n  api:\n    image: scratch\n",
        encoding="utf-8",
    )
    assert sc.main(tmp_path) == 0
    out = capsys.readouterr().out
    assert "AUTH_DISABLED_IN_IMAGE" not in out
    assert "DEV_MODE_IN_IMAGE" not in out


def test_untracked_dev_env_does_not_change_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "docker-compose.dev.yml").write_text(
        "services:\n"
        "  cortex:\n"
        "    image: scratch\n"
        "    env_file:\n"
        "      - path: dev.env\n"
        "        required: false\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("dev.env\n", encoding="utf-8")
    _git(tmp_path, "init")
    _git(tmp_path, "add", "docker-compose.dev.yml", ".gitignore")
    assert sc.main(tmp_path) == 0
    before = capsys.readouterr().out
    assert "DEV_MODE_IN_IMAGE" not in before
    assert "AUTH_DISABLED_IN_IMAGE" not in before
    (tmp_path / "dev.env").write_text(
        "CORTEX_DEV_MODE=1\nDMS_AUTH_DISABLED=1\n",
        encoding="utf-8",
    )
    assert sc.main(tmp_path) == 0
    after = capsys.readouterr().out
    assert after == before


def test_tracked_shipped_compose_dev_mode_environment_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "compose.ship.yml").write_text(
        "services:\n  api:\n    image: scratch\n    environment:\n      CORTEX_DEV_MODE: \"1\"\n",
        encoding="utf-8",
    )
    _git(tmp_path, "init")
    _git(tmp_path, "add", "compose.ship.yml")
    assert sc.main(tmp_path) == 1
    out = capsys.readouterr().out
    assert "DEV_MODE_IN_IMAGE" in out
    assert "via environment" in out
    assert "compose.ship.yml" in out


def test_tracked_shipped_compose_env_file_dev_mode_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "tracked.env").write_text("CORTEX_DEV_MODE=1\n", encoding="utf-8")
    (tmp_path / "compose.ship.yml").write_text(
        "services:\n  api:\n    image: scratch\n    env_file:\n      - tracked.env\n",
        encoding="utf-8",
    )
    _git(tmp_path, "init")
    _git(tmp_path, "add", "compose.ship.yml", "tracked.env")
    assert sc.main(tmp_path) == 1
    out = capsys.readouterr().out
    assert "DEV_MODE_IN_IMAGE" in out
    assert "via env_file" in out
    assert "compose.ship.yml" in out


def test_git_ls_files_failure_inside_repo_refuses_without_walking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "docker-compose.dev.yml").write_text(
        "services:\n"
        "  cortex:\n"
        "    image: scratch\n"
        "    env_file:\n"
        "      - path: dev.env\n"
        "        required: false\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("dev.env\n", encoding="utf-8")
    (tmp_path / "dev.env").write_text("CORTEX_DEV_MODE=1\n", encoding="utf-8")
    _git(tmp_path, "init")
    _git(tmp_path, "add", "docker-compose.dev.yml", ".gitignore")
    real_run = subprocess.run

    def _fail_ls_files(args, **kwargs):
        cmd = list(args) if isinstance(args, (list, tuple)) else []
        if "ls-files" in cmd:
            return subprocess.CompletedProcess(cmd, 1, b"", b"forced listing failure")
        return real_run(args, **kwargs)

    monkeypatch.setattr(sc.subprocess, "run", _fail_ls_files)
    assert sc.main(tmp_path) == 1
    out = capsys.readouterr().out
    assert "DEV_MODE_IN_IMAGE: GIT_LS_FILES_FAILED" in out
    assert "via env_file" not in out


def test_shipped_build_files_do_not_name_dev_overrides() -> None:
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
    )
    offenders: list[str] = []
    for rel in listed.stdout.decode().split("\0"):
        if not rel:
            continue
        name = Path(rel).name
        is_workflow = rel.startswith(".github/workflows/")
        is_dockerfile = name == "Dockerfile" or name.startswith("Dockerfile.")
        maybe_compose = rel != "docker-compose.dev.yml" and name.endswith((".yml", ".yaml"))
        if not (is_workflow or is_dockerfile or maybe_compose):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        is_shipped_compose = maybe_compose and ("services:" in text or "services :" in text)
        if not (is_workflow or is_dockerfile or is_shipped_compose):
            continue
        if _DEV_FILE_REF.search(text):
            offenders.append(rel)
    assert offenders == []
