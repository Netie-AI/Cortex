"""A1 — engine autostart dry-run + AirGPT ensure_engine helpers."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
START_PS1 = ROOT / "scripts" / "start_cortex_engine.ps1"
INSTALL_PS1 = ROOT / "scripts" / "install_engine_autostart.ps1"


def _windows_shell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(
    _windows_shell() is None or not START_PS1.is_file(),
    reason="powershell not on PATH or start script missing",
)
def test_start_cortex_engine_dry_run():
    shell = _windows_shell()
    assert shell is not None
    r = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(START_PS1),
            "-Port",
            "8010",
            "-DryRun",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr or r.stdout
    out = (r.stdout or "") + (r.stderr or "")
    assert "DryRun" in out
    assert "8010" in out
    hint = ROOT / "data" / "engine" / "cortex_api_url.txt"
    assert hint.is_file()
    assert "8010" in hint.read_text(encoding="utf-8")


@pytest.mark.skipif(
    _windows_shell() is None or not INSTALL_PS1.is_file(),
    reason="powershell not on PATH or install script missing",
)
def test_install_engine_autostart_dry_run():
    shell = _windows_shell()
    assert shell is not None
    r = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALL_PS1),
            "-Port",
            "8010",
            "-DryRun",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr or r.stdout
    out = (r.stdout or "") + (r.stderr or "")
    assert "DryRun" in out
    assert "CortexEngine.lnk" in out or "shortcut" in out.lower()
