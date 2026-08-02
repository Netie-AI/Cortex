"""A1 — engine autostart dry-run + AirGPT ensure_engine helpers."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
START_PS1 = ROOT / "scripts" / "start_cortex_engine.ps1"
INSTALL_PS1 = ROOT / "scripts" / "install_engine_autostart.ps1"

#: Two different gates, because these two scripts are Windows-specific to different
#: degrees. The GitHub Ubuntu runner ships pwsh, so "is a PowerShell present" is not the
#: same question as "will this script run here".
#:
#: start_cortex_engine.ps1 is portable - it runs green on the Linux runner under pwsh, so
#: it only needs an interpreter.
_POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
_NO_POWERSHELL = pytest.mark.skipif(
    _POWERSHELL is None, reason="no powershell/pwsh on PATH"
)

#: install_engine_autostart.ps1 is not portable: line 28 reads $env:APPDATA and writes
#: into the Windows Start Menu Startup folder. Under pwsh on Linux APPDATA is null and
#: Join-Path fails to bind. Gate it on the platform, not the interpreter - there is no
#: Startup folder to install into, so no assertion is being dodged (R-0002).
_NOT_WINDOWS = pytest.mark.skipif(
    sys.platform != "win32",
    reason="installs into the Windows Start Menu Startup folder via $env:APPDATA",
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


@_NOT_WINDOWS
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
