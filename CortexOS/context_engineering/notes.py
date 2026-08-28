"""Structured note-taking — agentic memory outside the context window."""
from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


def default_notes_path(workspace_root: str | Path | None) -> Path | None:
    if not workspace_root:
        return None
    root = Path(workspace_root)
    if not root.is_dir():
        return None
    airgpt = root / ".airgpt"
    airgpt.mkdir(parents=True, exist_ok=True)
    return airgpt / "NOTES.md"


@dataclass
class NoteStore:
    """Append-only NOTES.md with a small read-back window for re-injection."""

    path: Path
    max_read_chars: int = 3000

    def append(self, note: str, *, heading: str = "") -> dict:
        body = (note or "").strip()
        if not body:
            return {"ok": False, "error": "empty note"}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        title = (heading or "Note").strip()
        block = f"\n## {title} ({ts})\n{body}\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(block)
        return {"ok": True, "path": str(self.path), "chars": len(block)}

    def read_recent(self) -> str:
        if not self.path.is_file():
            return ""
        try:
            text = self.path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        if len(text) <= self.max_read_chars:
            return text.strip()
        return text[-self.max_read_chars :].strip()

    def replace_section(self, heading: str, body: str) -> dict:
        """Upsert a ## section by heading (first match). Creates file if missing."""
        h = (heading or "").strip()
        b = (body or "").strip()
        if not h or not b:
            return {"ok": False, "error": "heading and body required"}
        existing = ""
        if self.path.is_file():
            try:
                existing = self.path.read_text(encoding="utf-8", errors="ignore")
            except OSError as e:
                return {"ok": False, "error": str(e)[:200]}
        marker = f"## {h}"
        lines = existing.splitlines(keepends=True) if existing else []
        start = next((i for i, ln in enumerate(lines) if ln.startswith(marker)), -1)
        chunk = f"## {h}\n{b}\n\n"
        if start < 0:
            new_text = (existing.rstrip() + "\n\n" if existing.strip() else "") + chunk
        else:
            end = start + 1
            while end < len(lines) and not lines[end].startswith("## "):
                end += 1
            new_text = "".join(lines[:start]) + chunk + "".join(lines[end:])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(new_text, encoding="utf-8")
        return {"ok": True, "path": str(self.path), "heading": h}

    def bullets(self, items: Iterable[str], *, heading: str = "Objectives") -> dict:
        lines = [f"- {x.strip()}" for x in items if (x or "").strip()]
        if not lines:
            return {"ok": False, "error": "no items"}
        return self.replace_section(heading, "\n".join(lines))
