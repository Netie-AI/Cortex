"""Optional git worktree for a spawned teammate. Gas Town analog, no Beads."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any


def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def _safe(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-")[:40] or "agent"


def source_repo() -> Path | None:
    raw = (os.environ.get("CREW_WORKTREE_ROOT") or os.environ.get("CREW_WORKTREE_FROM") or "").strip()
    if not raw:
        return None
    root = Path(raw)
    if (root / ".git").exists() or (root / ".git").is_file():
        return root
    return None


def attach_worktree(name: str, data_dir: Path, *, source: Path | None = None) -> dict[str, Any]:
    """Checkout dest as a git worktree. ok=false means spawn continues without one."""
    root = source or source_repo()
    if root is None:
        return {"ok": False, "path": "", "error": "CREW_WORKTREE_ROOT unset; worktree skipped"}
    dest = (Path(data_dir) / "worktrees" / _safe(name)).resolve()
    if dest.exists() and any(dest.iterdir()):
        return {"ok": True, "path": str(dest), "error": ""}
    dest.parent.mkdir(parents=True, exist_ok=True)
    branch = "crew-" + _safe(name)
    code, out = _git(["worktree", "add", "-b", branch, str(dest), "HEAD"], cwd=root)
    if code != 0:
        return {"ok": False, "path": "", "error": out or "git worktree add failed"}
    return {"ok": True, "path": str(dest), "error": ""}