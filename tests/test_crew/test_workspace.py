"""Jailed per-space workspace. Escape attempts fail closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from CortexOS.crew.workspace import WorkspaceError, as_error, workspace_for


def test_workspace_write_read_and_refuses_escape(tmp_path: Path) -> None:
    ws = workspace_for(tmp_path / "crew", "space-1")
    assert "wrote notes.md" in ws.write("notes.md", "ticket body here")
    assert "ticket body here" in ws.read("notes.md")
    assert "notes.md" in ws.glob("*.md")
    with pytest.raises(WorkspaceError, match="escape"):
        ws.read("../secrets.txt")
    with pytest.raises(WorkspaceError, match="absolute"):
        ws.write("C:/Windows/x.txt", "no")
    assert "DENIED" in as_error(WorkspaceError("path escapes workspace"))
