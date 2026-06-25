#!/usr/bin/env python3
"""Generate audience-specific handoff blocks for Claude supervisor or Cursor builder."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAUDE_FILE = ROOT / "CLAUDE_HANDOFF.md"
CURSOR_FILE = ROOT / "CURSOR_HANDOFF.md"
CONTEXT_FILE = ROOT / "CONTEXT.md"
STATUS_FILE = ROOT / "STATUS.md"


def _read(path: Path) -> str:
    if not path.is_file():
        return f"<!-- missing: {path.name} -->\n"
    return path.read_text(encoding="utf-8").strip()


def _pytest_summary() -> str:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        lines = (r.stdout or r.stderr or "").strip().splitlines()
        return lines[-1] if lines else "pytest: no output"
    except Exception as exc:
        return f"pytest: skipped ({exc})"


def _patch_test_snapshot(content: str, summary: str) -> str:
    """Replace test snapshot line in CLAUDE_HANDOFF if present."""
    if "Run locally:" in content:
        return re.sub(
            r"Run locally: `pytest -q` — expect .*",
            f"Run locally: `pytest -q` — **{summary}**",
            content,
            count=1,
        )
    return content


def _stamp(content: str) -> str:
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if "**Auto-sync:**" in content:
        return re.sub(
            r"\*\*Auto-sync:\*\*.*",
            f"**Auto-sync:** run `python scripts/handoff.py --write` after every ship or gate. "
            f"Last generated: {ts}",
            content,
            count=1,
        )
    return f"<!-- generated {ts} -->\n{content}"


def build_claude() -> str:
    body = _read(CLAUDE_FILE)
    body = _patch_test_snapshot(body, _pytest_summary())
    return body


def build_cursor() -> str:
    return _read(CURSOR_FILE)


def build_legacy() -> str:
    """Backward-compatible combined block."""
    parts = [
        "# Cortex handoff — legacy combined",
        "",
        _read(CONTEXT_FILE),
        "",
        "---",
        "",
        _read(STATUS_FILE),
        "",
        "---",
        "",
        "## Test snapshot",
        "```",
        _pytest_summary(),
        "```",
        "",
        "## Audience-specific handoffs",
        "- Claude supervisor: `CLAUDE_HANDOFF.md` or `--claude`",
        "- Cursor builder: `CURSOR_HANDOFF.md` or `--cursor`",
        "",
        "See docs/dms/SUPERVISOR_GATE.md for gate template.",
    ]
    return "\n".join(parts)


def write_files() -> None:
    summary = _pytest_summary()
    claude = _stamp(_patch_test_snapshot(_read(CLAUDE_FILE), summary))
    CLAUDE_FILE.write_text(claude + "\n", encoding="utf-8")
    cursor = _stamp(_read(CURSOR_FILE))
    CURSOR_FILE.write_text(cursor + "\n", encoding="utf-8")
    print(f"Updated {CLAUDE_FILE.name} and {CURSOR_FILE.name}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cortex handoff generator")
    parser.add_argument("--claude", action="store_true", help="Emit Claude supervisor handoff")
    parser.add_argument("--cursor", action="store_true", help="Emit Cursor builder handoff")
    parser.add_argument("--write", action="store_true", help="Refresh handoff files with test snapshot")
    args = parser.parse_args()

    if args.write:
        write_files()

    if args.claude:
        block = build_claude()
    elif args.cursor:
        block = build_cursor()
    elif args.write and not (args.claude or args.cursor):
        return
    else:
        block = build_legacy()

    print(block)
    try:
        import pyperclip

        pyperclip.copy(block)
        print("\n[Copied to clipboard via pyperclip]", file=sys.stderr)
    except Exception:
        pass


if __name__ == "__main__":
    main()
