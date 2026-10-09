"""#310 CX-3-pre license for the FreeRoute dual-write guards.

Founder GO via Lead (2026-10-06): engine-reached crew modules move to
``CortexOS/agentplane`` as a pure relocation. On the licensed branch only, a
guarded module may appear in the diff if the old path is exactly the re-export
shim and the new path is the base file with nothing changed except
``CortexOS.crew`` import lines repointed (plus the pinned data-dir line edits).
Any other edit needs its own exception.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "c7469da4e87cb49f56930242189b10817cba7d04"
BRANCH = "cursor/cx3-pre-relocate-crew-5c03"
MODULES = frozenset(
    {
        "board",
        "certified_serve",
        "config",
        "connectors",
        "cot_climb",
        "engine_bridge",
        "estate",
        "freeroute",
        "github",
        "inbox",
        "insights",
        "keys",
        "l2_serve",
        "llm",
        "openvault",
        "policy",
        "prompt_harness_climb",
        "shell",
        "ship_gate",
    }
)
LINE_EDITS = {
    "config": (
        'Path(__file__).parent / "ui"',
        'Path(__file__).parent.parent / "crew" / "ui"',
    ),
    "board": (
        'Path(__file__).resolve().parent / "skill_packs"',
        'Path(__file__).resolve().parent.parent / "crew" / "skill_packs"',
    ),
}
SHIM = '''"""Re-export shim: moved to :mod:`CortexOS.agentplane.{name}` (CX-3-pre #310).

Aliases this name to the real module, so module state and monkeypatches stay
shared. CX-3 deletes this shim with the crew server.
"""

import sys

from CortexOS.agentplane import {name} as _moved

sys.modules[__name__] = _moved
'''


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )


def _branch_name() -> str:
    from_ci = os.environ.get("GITHUB_HEAD_REF", "").strip()
    if from_ci:
        return from_ci
    result = _git("branch", "--show-current")
    return result.stdout.strip() if result.returncode == 0 else ""


def _show(rev: str, path: str) -> str:
    shown = _git("show", f"{rev}:{path}")
    assert shown.returncode == 0, shown.stderr
    return shown.stdout


def relocated(base_source: str, name: str) -> str:
    """The only content #310 allows at ``CortexOS/agentplane/<name>.py``."""
    out = "".join(
        line.replace("CortexOS.crew", "CortexOS.agentplane", 1)
        if line.lstrip().startswith(("from CortexOS.crew", "import CortexOS.crew"))
        else line
        for line in base_source.splitlines(keepends=True)
    )
    if name in LINE_EDITS:
        old, new = LINE_EDITS[name]
        assert out.count(old) == 1, f"#310 data-dir line for {name}.py not found once"
        out = out.replace(old, new)
    return out


def is_exact_move(path: str) -> bool:
    if _branch_name() != BRANCH:
        return False
    name = Path(path).stem
    if name not in MODULES or path not in {
        f"CortexOS/crew/{name}.py",
        f"CortexOS/agentplane/{name}.py",
    }:
        return False
    if _git("cat-file", "-e", f"{BASE}^{{commit}}").returncode != 0:
        _git("fetch", "--depth=1", "origin", BASE)  # shallow CI clone
    expected = relocated(_show(BASE, f"CortexOS/crew/{name}.py"), name)
    assert _show("HEAD", f"CortexOS/agentplane/{name}.py") == expected, (
        f"#310 licenses only a pure relocation of {name}.py; any other edit "
        "needs its own exception"
    )
    assert _show("HEAD", f"CortexOS/crew/{name}.py") == SHIM.format(name=name), (
        f"#310 shim at CortexOS/crew/{name}.py must be the exact re-export shim"
    )
    return True
