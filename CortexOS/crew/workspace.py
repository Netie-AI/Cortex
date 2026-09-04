"""Per-space jailed filesystem. Original Cortex code.

DeepAgents MIT pattern (filesystem tools + permission at the tool boundary),
not a vendor copy and not a second dag_runner. Every path is resolved under
``data/crew/spaces/<id>/ws``. Escape attempts fail closed.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

MAX_BYTES = 256 * 1024
MAX_LIST = 200
READ_LINES = 200


class WorkspaceError(ValueError):
    """Path or size refused. The agent must report this, not retry blindly."""


class SpaceWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, rel: str) -> Path:
        raw = (rel or "").strip().replace("\\", "/").lstrip("/")
        if not raw or raw in {".", "./"}:
            return self.root
        if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
            raise WorkspaceError("absolute paths are denied")
        parts = [p for p in raw.split("/") if p and p != "."]
        if any(p == ".." for p in parts):
            raise WorkspaceError("path escapes workspace")
        candidate = (self.root.joinpath(*parts)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError("path escapes workspace") from exc
        return candidate

    def ls(self, rel: str = ".") -> str:
        target = self.resolve(rel)
        if not target.exists():
            raise WorkspaceError(f"missing: {rel or '.'}")
        if target.is_file():
            return f"file {rel} ({target.stat().st_size} bytes)"
        rows: list[str] = []
        for child in sorted(target.iterdir())[:MAX_LIST]:
            kind = "dir" if child.is_dir() else "file"
            size = child.stat().st_size if child.is_file() else 0
            rows.append(f"{kind}\t{child.name}\t{size}")
        if not rows:
            return "(empty)"
        extra = ""
        if sum(1 for _ in target.iterdir()) > MAX_LIST:
            extra = f"\n(truncated at {MAX_LIST})"
        return "\n".join(rows) + extra

    def read(self, rel: str, offset: int = 0, limit: int = READ_LINES) -> str:
        path = self.resolve(rel)
        if not path.is_file():
            raise WorkspaceError(f"not a file: {rel}")
        if path.stat().st_size > MAX_BYTES:
            raise WorkspaceError(f"file exceeds {MAX_BYTES} bytes; glob or split it")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, int(offset))
        stop = start + max(1, min(int(limit), 2000))
        chunk = lines[start:stop]
        header = f"# {rel} lines {start + 1}-{start + len(chunk)} of {len(lines)}\n"
        return header + "\n".join(chunk)

    def write(self, rel: str, content: str) -> str:
        if not (rel or "").strip() or (rel or "").strip() in {".", "./"}:
            raise WorkspaceError("need a file path")
        path = self.resolve(rel)
        if path.exists() and path.is_dir():
            raise WorkspaceError(f"is a directory: {rel}")
        data = content if isinstance(content, str) else str(content)
        encoded = data.encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise WorkspaceError(f"write exceeds {MAX_BYTES} bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return f"wrote {rel} ({len(encoded)} bytes)"

    def edit(self, rel: str, old: str, new: str) -> str:
        path = self.resolve(rel)
        if not path.is_file():
            raise WorkspaceError(f"not a file: {rel}")
        body = path.read_text(encoding="utf-8", errors="replace")
        if old not in body:
            raise WorkspaceError("old_string not found")
        if body.count(old) != 1:
            raise WorkspaceError("old_string matches more than once; make it unique")
        return self.write(rel, body.replace(old, new, 1))

    def glob(self, pattern: str) -> str:
        needle = (pattern or "*").replace("\\", "/")
        hits: list[str] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if fnmatch.fnmatch(rel, needle) or fnmatch.fnmatch(path.name, needle):
                hits.append(rel)
            if len(hits) >= MAX_LIST:
                break
        if not hits:
            return "(no matches)"
        return "\n".join(hits)


def workspace_for(data_dir: Path, space_id: str) -> SpaceWorkspace:
    return SpaceWorkspace(data_dir / "spaces" / space_id / "ws")


def as_error(exc: BaseException) -> str:
    return f"DENIED: {exc}"


def preview_large(text: str, *, head: int = 5, tail: int = 5) -> dict[str, Any]:
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return {"preview": text, "offloaded": False}
    shown = (
        "\n".join(lines[:head])
        + f"\n... [{len(lines) - head - tail} lines omitted] ...\n"
        + "\n".join(lines[-tail:])
    )
    return {"preview": shown, "offloaded": True, "lines": len(lines)}
