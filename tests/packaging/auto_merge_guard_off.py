"""pytest plugin: revert one auto-merge guard, named by ``AUTO_MERGE_GUARD_OFF``.

Loaded only by test_auto_merge_guard_mutations.py in a subprocess. It applies the
source edits below to scripts/auto_merge_if_perfect.py and installs the result as
that module, so the named must-fail tests run against the script without the
guard. Each edit must match exactly once; a stale edit is an error, never a no-op.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "auto_merge_if_perfect.py"
MODULE = "scripts.auto_merge_if_perfect"


def _always_pass(signature: str) -> tuple[str, str]:
    return signature, signature + "    return None\n"


_CLEAR_LOGIN = "        if _login(comment) not in CLEAR_AUTHORS:\n"
_AGREE_LOGIN = "        if _login(comment) not in AGREE_AUTHORS:\n"
_LIVE_LOGIN = "if isinstance(c, dict) and _login(c) in trusted"

MUTATIONS: dict[str, list[tuple[str, str]]] = {
    "tier": [_always_pass("def _tier_refusal(pr: dict[str, Any]) -> str | None:\n")],
    "tier_full": [
        (
            "    if any(_TIER_FULL.fullmatch(name) for name in labels) or _TIER_FULL.search(body):",
            "    if False:",
        )
    ],
    "tier_first_lines": [("if line.strip()][:TIER_LINES]", "if line.strip()]")],
    "draft": [
        _always_pass("def _draft_refusal(pr: dict[str, Any]) -> str | None:\n"),
        ('    if pr.get("isDraft"):\n        return "skip"\n', ""),
    ],
    "required_checks": [
        ('    for check in pr.get("statusCheckRollup") or []:', "    for check in []:"),
        ("    for name in REQUIRED:\n", "    for name in ():\n"),
    ],
    "checks_exact_head": [
        (
            'if isinstance(run, dict) and str(run.get("head_sha") or "") == head',
            "if isinstance(run, dict)",
        )
    ],
    "clear": [
        _always_pass(
            "def _clear_refusal(comments: list[dict[str, Any]], head: str) -> str | None:\n"
        )
    ],
    "clear_exact_head": [
        (
            "        if _names_head(body, head):\n            return None\n",
            "        if True:\n            return None\n",
        )
    ],
    "clear_authors": [(_CLEAR_LOGIN, "        if False:\n"), (_LIVE_LOGIN, "if True")],
    "agree": [
        (
            ") -> str | None:\n    author = author_family(pr)\n",
            ") -> str | None:\n    return None\n    author = author_family(pr)\n",
        )
    ],
    "agree_exact_head": [("        if not _names_head(body, head):\n            continue\n", "")],
    "agree_family": [("        if reviewer == author:\n            continue\n", "")],
    "agree_model_declared": [("        if reviewer is None:\n            continue\n", "")],
    "author_family_known": [("    if author is None:\n", "    if False:\n")],
    "agree_authors": [(_AGREE_LOGIN, "        if False:\n"), (_LIVE_LOGIN, "if True")],
    "veto": [("            after = _id(comment)\n", "            pass\n")],
    "match_head_commit": [('    cmd += ["--match-head-commit", head_sha]\n', "")],
}


def mutated_source(guard: str) -> str:
    src = SCRIPT.read_text(encoding="utf-8")
    for old, new in MUTATIONS[guard]:
        count = src.count(old)
        if count != 1:
            raise RuntimeError(f"guard {guard!r}: edit matched {count} times: {old!r}")
        src = src.replace(old, new)
    return src


def pytest_configure(config: Any) -> None:
    guard = os.environ.get("AUTO_MERGE_GUARD_OFF", "")
    if guard not in MUTATIONS:
        raise RuntimeError(f"unknown AUTO_MERGE_GUARD_OFF={guard!r}")
    if MODULE in sys.modules:
        raise RuntimeError(f"{MODULE} imported before the guard was removed")
    module = types.ModuleType(MODULE)
    module.__file__ = str(SCRIPT)
    sys.modules[MODULE] = module
    exec(compile(mutated_source(guard), str(SCRIPT), "exec"), module.__dict__)
