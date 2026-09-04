"""Dual-path crew exec: laptop allowlist vs Cloudflare Computer isolate (preview)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from CortexOS.crew import config, policy
from CortexOS.crew.config import (
    BACKEND_CF_COMPUTER,
    BACKEND_LAPTOP,
    FLAG_CF_COMPUTER,
    CrewSettings,
)
from CortexOS.crew.shell import CrewShell, isolate_env


def _settings(tmp_path: Path, **kwargs: Any) -> CrewSettings:
    data = tmp_path / "crew"
    data.mkdir(parents=True, exist_ok=True)
    values: dict[str, Any] = {
        "port": 0,
        "engine_url": "http://127.0.0.1:9",
        "engine_session": "demo",
        "data_dir": data,
        "master_computer_control": False,
    }
    values.update(kwargs)
    return CrewSettings(**values)


def _ok_run(
    argv: list[str], timeout: float = 20.0, cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 0, stdout="ok " + " ".join(argv), stderr="")


def test_load_settings_defaults_laptop_and_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "crew"))
    monkeypatch.delenv("CREW_RUNTIME_BACKEND", raising=False)
    monkeypatch.delenv(FLAG_CF_COMPUTER, raising=False)
    monkeypatch.delenv("CREW_CF_COMPUTER_TOKEN", raising=False)
    monkeypatch.delenv("CREW_CF_COMPUTER_URL", raising=False)
    monkeypatch.delenv("CREW_CF_COMPUTER_FORWARD_HOST_ENV", raising=False)
    settings = config.load_settings()
    assert settings.runtime_backend == BACKEND_LAPTOP
    assert settings.cf_computer_enabled is False
    assert settings.cf_computer_forward_host_env is False
    assert settings.cf_computer_token == ""


def test_operator_env_selects_isolate_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "crew"))
    monkeypatch.setenv("CREW_RUNTIME_BACKEND", "cloudflare-computer")
    monkeypatch.setenv(FLAG_CF_COMPUTER, "1")
    settings = config.load_settings()
    assert settings.runtime_backend == BACKEND_CF_COMPUTER
    assert settings.cf_computer_enabled is True


def test_decide_runtime_allowlist_and_mutating() -> None:
    allow, reason = policy.decide_runtime(
        ["gh", "pr", "list", "--limit", "5"],
        backend=BACKEND_LAPTOP,
        isolate_enabled=False,
    )
    assert allow == policy.ALLOW and "laptop" in reason

    auth, _ = policy.decide_runtime(
        ["gh", "auth", "status"], backend=BACKEND_LAPTOP, isolate_enabled=False
    )
    assert auth == policy.ALLOW

    denied, why = policy.decide_runtime(
        ["bash", "-c", "id"], backend=BACKEND_LAPTOP, isolate_enabled=False
    )
    assert denied == policy.DENY and "allowlist" in why

    confirm, cwhy = policy.decide_runtime(
        ["gh", "pr", "merge", "1"], backend=BACKEND_LAPTOP, isolate_enabled=False
    )
    assert confirm == policy.CONFIRM and "merge" in cwhy

    after, _ = policy.decide_runtime(
        ["gh", "pr", "merge", "1"],
        backend=BACKEND_LAPTOP,
        isolate_enabled=False,
        approved=True,
    )
    assert after == policy.ALLOW

    off, flag_why = policy.decide_runtime(
        ["gh", "pr", "list"],
        backend=BACKEND_CF_COMPUTER,
        isolate_enabled=False,
    )
    assert off == policy.DENY and FLAG_CF_COMPUTER in flag_why and "not production" in flag_why


def test_laptop_exec_uses_injected_runner(tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def runner(
        argv: list[str], timeout: float = 20.0, cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return _ok_run(argv, timeout=timeout, cwd=cwd)

    crew_shell = CrewShell(_settings(tmp_path), runner=runner)
    result = crew_shell.exec(["gh", "pr", "list", "--limit", "5"])
    assert result.ok is True
    assert result.backend == BACKEND_LAPTOP
    assert result.production is False
    assert seen == [["gh", "pr", "list", "--limit", "5"]]
    assert "gh pr list" in result.stdout


def test_laptop_unknown_binary_never_runs(tmp_path: Path) -> None:
    def boom(*_a: Any, **_k: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("runner must not run a denied argv")

    crew_shell = CrewShell(_settings(tmp_path), runner=boom)
    result = crew_shell.exec(["bash", "-c", "cat /etc/passwd"])
    assert result.denied is True
    assert result.ok is False
    assert "allowlist" in result.reason


def test_isolate_flag_off_does_not_post(tmp_path: Path) -> None:
    def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise AssertionError("must not call preview gateway when flag is off")

    settings = _settings(
        tmp_path, runtime_backend=BACKEND_CF_COMPUTER, cf_computer_enabled=False
    )
    crew_shell = CrewShell(settings, transport=boom)
    result = crew_shell.exec(["gh", "pr", "list"])
    assert result.denied is True
    assert FLAG_CF_COMPUTER in result.reason
    assert result.preview is True
    assert result.production is False


def test_isolate_without_url_is_preview_unavailable(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, runtime_backend=BACKEND_CF_COMPUTER, cf_computer_enabled=True
    )
    crew_shell = CrewShell(settings)
    result = crew_shell.exec(["gh", "repo", "list", "Netie-AI"])
    assert result.ok is False
    assert result.preview is True
    assert result.production is False
    assert "not production" in result.reason
    assert "CREW_CF_COMPUTER_URL" in result.reason


def test_isolate_mock_does_not_forward_host_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host-must-not-leak")
    monkeypatch.setenv("GH_TOKEN", "ghp-host-must-not-leak")
    monkeypatch.setenv("PATH", "/usr/bin")
    posted: dict[str, Any] = {}

    def transport(url: str, body: dict[str, Any], token: str, timeout: float) -> dict[str, Any]:
        posted["url"] = url
        posted["body"] = body
        posted["token"] = token
        return {
            "ok": True,
            "status": "completed",
            "exitCode": 0,
            "stdout": "isolated",
            "stderr": "",
        }

    settings = _settings(
        tmp_path,
        runtime_backend=BACKEND_CF_COMPUTER,
        cf_computer_enabled=True,
        cf_computer_url="https://preview.example/computer",
        cf_computer_token="cf-preview-token",
        cf_computer_forward_host_env=False,
    )
    crew_shell = CrewShell(settings, transport=transport)
    result = crew_shell.exec(
        ["gh", "pr", "list"],
        env={"OPENAI_API_KEY": "sk-explicit", "CI": "1", "GH_TOKEN": "nope"},
    )
    assert result.ok is True
    assert result.preview is True
    assert result.production is False
    env = posted["body"]["env"]
    assert env.get("CI") == "1"
    assert "OPENAI_API_KEY" not in env
    assert "GH_TOKEN" not in env
    assert "sk-host-must-not-leak" not in str(posted["body"])
    assert "cf-preview-token" not in str(posted["body"]["env"])
    assert posted["token"] == "cf-preview-token"
    assert posted["url"].endswith("/runtime/exec")
    assert posted["body"]["backend"] == "worker-shell"
    assert "OPENAI_API_KEY" not in result.isolate_env_keys


def test_forward_host_env_still_strips_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-still-blocked")
    monkeypatch.setenv("LANG", "C")
    out = isolate_env(None, forward_host=True)
    assert out.get("LANG") == "C"
    assert "OPENAI_API_KEY" not in out
    assert "CREW_CF_COMPUTER_TOKEN" not in out


def test_public_status_never_includes_token(tmp_path: Path) -> None:
    settings = _settings(tmp_path, cf_computer_token="super-secret-token")
    status = CrewShell(settings).public()
    blob = str(status)
    assert "super-secret-token" not in blob
    assert status["flag"] == FLAG_CF_COMPUTER
    assert status["production"] is False
    assert status["preview"] is True
    assert status["token_configured"] is True
    assert status["source"] == "https://github.com/cloudflare/computer"
