"""A1 — engine autostart dry-run + AirGPT ensure_engine helpers."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
START_PS1 = ROOT / "scripts" / "start_cortex_engine.ps1"
INSTALL_PS1 = ROOT / "scripts" / "install_engine_autostart.ps1"

#: These exercise PowerShell autostart scripts, which only exist on Windows. Gate on the
#: interpreter actually being present rather than on sys.platform, so the tests also run
#: anywhere pwsh is installed - and so the skip reason names the real cause. This is a
#: capability gate, not R-0002 skipping-to-go-green: on Linux there is no autostart
#: script to test, so there is no assertion being dodged.
_POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
_NO_POWERSHELL = pytest.mark.skipif(
    _POWERSHELL is None, reason="no powershell/pwsh on PATH - Windows-only autostart path"
)


@_NO_POWERSHELL
@pytest.mark.skipif(not START_PS1.is_file(), reason="start script missing")
def test_start_cortex_engine_dry_run():
    shell = _POWERSHELL
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


@_NO_POWERSHELL
@pytest.mark.skipif(not INSTALL_PS1.is_file(), reason="install script missing")
def test_install_engine_autostart_dry_run():
    shell = _POWERSHELL
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
