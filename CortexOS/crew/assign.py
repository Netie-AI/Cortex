"""Crew-local ticket binds. Not CLAIMS seating. Not GitHub assignees.

Control GET-displays these on the belt. Crew executes via /assign: local bind,
then A2A brief + a run. Ticket Runner still owns CLAIMS.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from CortexOS.crew import github as github_mod

_FILE = "assignments.json"
_CAP = 80


def _path(data_dir: Path) -> Path:
    return data_dir / _FILE


def load(data_dir: Path) -> list[dict[str, Any]]:
    path = _path(data_dir)
    if not path.is_file():
        return []
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = blob if isinstance(blob, list) else blob.get("assignments") if isinstance(blob, dict) else []
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("spec") or "").strip():
            out.append(row)
    return out[:_CAP]


def save(data_dir: Path, rows: list[dict[str, Any]]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    _path(data_dir).write_text(
        json.dumps(rows[:_CAP], ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def public(data_dir: Path) -> list[dict[str, Any]]:
    """Belt JSON. Control must not POST this."""
    out: list[dict[str, Any]] = []
    for row in load(data_dir):
        spec = str(row.get("spec") or "").strip()
        out.append(
            {
                "spec": spec,
                "agent": str(row.get("agent") or ""),
                "space_id": str(row.get("space_id") or ""),
                "title": str(row.get("title") or spec),
                "ready": github_mod.seated_claim(spec) is None,
            }
        )
    return out


def bind(
    data_dir: Path,
    *,
    space_id: str,
    spec: str,
    agent_id: str,
    agent_name: str,
    title: str = "",
) -> dict[str, Any]:
    parsed = github_mod.parse_issue_spec(spec)
    if parsed is None:
        return {
            "ok": False,
            "detail": "DENIED: /assign owner/repo#n | Name",
        }
    seated = github_mod.seated_claim(spec)
    if seated is not None:
        return {
            "ok": False,
            "detail": (
                f"DENIED: {seated.get('ticket')} is SEATED "
                f"({seated.get('owner_pr')}). Ticket Runner owns the seat. "
                "Crew does not steal it."
            ),
        }
    who = (agent_name or "").strip()
    if not who or who.lower() == "manager":
        return {
            "ok": False,
            "detail": "DENIED: assign a teammate, not Manager",
        }
    canon = github_mod.canonical_spec(spec)
    rows = [r for r in load(data_dir) if str(r.get("spec") or "") != canon]
    row = {
        "spec": canon,
        "space_id": space_id,
        "agent_id": agent_id,
        "agent": who,
        "title": (title or canon).strip()[:200],
    }
    rows.append(row)
    save(data_dir, rows)
    return {
        "ok": True,
        "spec": canon,
        "agent": who,
        "agent_id": agent_id,
        "title": row["title"],
        "law": (
            "Local bind only. Did not write CLAIMS.json. Did not set a GitHub assignee."
        ),
    }


def release(data_dir: Path, spec: str) -> None:
    canon = github_mod.canonical_spec(spec)
    if not canon:
        return
    keep = [r for r in load(data_dir) if str(r.get("spec") or "") != canon]
    save(data_dir, keep)
