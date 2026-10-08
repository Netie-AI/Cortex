"""IMAGE-AUTH-01: auth-off is refused outside dev mode, and images cannot set it.

(a) ``DMS_AUTH_DISABLED`` without ``CORTEX_DEV_MODE`` raises
``AUTH_DISABLED_WITHOUT_DEV_MODE`` before FastAPI exists, so no route serves.
(b) A fixture Dockerfile ``ENV`` or a fixture compose ``environment`` / ``env_file``
makes ``scripts/check_supply_chain.py`` exit 1 with ``AUTH_DISABLED_IN_IMAGE``.
The same three channels exit 1 with ``DEV_MODE_IN_IMAGE`` for ``CORTEX_DEV_MODE``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts import check_supply_chain as sc

ROOT = Path(__file__).resolve().parents[2]


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
