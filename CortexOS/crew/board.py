"""Netie ticket board from CLAIMS.json + RUNTIME.md. GitHub stays SoT.

Crew reads the watchdog inbox. It does not seat writers and it does not spawn
one Cursor cloud agent per issue (FLEET.md).
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_DEFAULT_CLAIMS = Path(r"D:\Netie\Internal\Agents\CLAIMS.json")
_DEFAULT_RUNTIME = Path(r"D:\Netie\Internal\Agents\RUNTIME.md")


def _claims_path() -> Path:
    return Path(os.environ.get("CREW_CLAIMS", str(_DEFAULT_CLAIMS)))


def _runtime_path() -> Path:
    return Path(os.environ.get("CREW_RUNTIME", str(_DEFAULT_RUNTIME)))


def snapshot() -> dict[str, Any]:
    claims_file = _claims_path()
    runtime_file = _runtime_path()
    tickets: list[dict[str, Any]] = []
    if claims_file.is_file():
        try:
            blob = json.loads(claims_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            blob = {}
        for row in blob.get("tickets") or []:
            if not isinstance(row, dict):
                continue
            tickets.append(
                {
                    "ticket": row.get("ticket"),
                    "repo": row.get("repo"),
                    "owner_pr": row.get("owner_pr"),
                    "head": row.get("head"),
                    "role": row.get("role"),
                    "may_write": bool(row.get("may_write")),
                }
            )
    runtime_head = ""
    if runtime_file.is_file():
        try:
            runtime_head = "\n".join(runtime_file.read_text(encoding="utf-8").splitlines()[:24])
        except OSError:
            runtime_head = ""
    seated = [t for t in tickets if t.get("role") == "SEATED"]
    unseated = [t for t in tickets if t.get("role") != "SEATED"]
    return {
        "ok": True,
        "claims": str(claims_file),
        "n": len(tickets),
        "seated": len(seated),
        "unseated": len(unseated),
        "tickets": tickets[:80],
        "runtime_head": runtime_head,
        "law": "Ticket Runner seats existing writers. Do not spawn one cloud agent per issue. Human is money/decision.",
    }


PACKS_DIR = Path(__file__).resolve().parent / "skill_packs"


def _skill_slug(title: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (title or "").strip())[:60] or "skill"


def list_skills(folder: Path) -> list[dict[str, str]]:
    if not folder.is_dir():
        return []
    from CortexOS.crew.skill_index import index_for

    index = index_for(folder)
    out: list[dict[str, str]] = []
    for entry in index.entries:
        out.append(
            {
                "title": entry.name,
                "path": entry.rel,
                "head": (entry.description or "")[:120],
                "labels": ",".join(entry.labels),
            }
        )
    return out


def save_skill(
    folder: Path,
    title: str,
    body: str,
    *,
    labels: Sequence[str] = (),
    source: str = "",
) -> dict[str, str]:
    """Write one Teach skill. Local files win going forward; this slug overwrites."""
    from CortexOS.crew.skill_index import clear_cache

    folder.mkdir(parents=True, exist_ok=True)
    slug = _skill_slug(title)
    path = folder / f"{slug or 'skill'}.md"
    tagged = _normalize_labels(labels)
    text = _with_frontmatter(title, body or "", tagged, source or "")
    path.write_text(text, encoding="utf-8")
    clear_cache()
    return {
        "ok": "true",
        "path": str(path),
        "title": title.strip() or slug,
        "slug": slug,
        "labels": ",".join(tagged),
        "source": (source or "").strip(),
    }


def _normalize_labels(labels: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in labels:
        for part in str(raw).split(","):
            slug = _skill_slug(part.strip().lower()).strip("-_")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            out.append(slug)
            if len(out) >= 8:
                return out
    return out


def _with_frontmatter(title: str, body: str, labels: Sequence[str], source: str) -> str:
    raw = (body or "").strip()
    if raw.startswith("---"):
        return raw + ("\n" if not raw.endswith("\n") else "")
    desc = ""
    for line in raw.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            desc = stripped[:140]
            break
    if not desc:
        desc = (title or "skill").strip()[:140]
    name = (title or "skill").strip()[:60]
    lines = ["---", f"name: {name}", f"description: {desc}"]
    if labels:
        lines.append("labels: " + ", ".join(labels))
    if source.strip():
        lines.append("source: " + source.strip()[:300])
    lines.append("---")
    lines.append("")
    lines.append(raw)
    lines.append("")
    return "\n".join(lines)


def read_skill(folder: Path, title: str) -> str:
    slug = _skill_slug(title)
    for root in (folder, PACKS_DIR):
        path = root / f"{slug}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.strip():
            return text
    return ""


DEFAULT_TONE = """ASCII only. Short. Grok-bot: name the next owner, then stop.
Do not spawn a cloud swarm. Human is money and decision authority.
"""


def ensure_default_tone(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "tone.md"
    if not path.exists():
        path.write_text(DEFAULT_TONE, encoding="utf-8")
    return path


def ensure_skill_packs(folder: Path) -> list[Path]:
    """Copy shipped packs into the Teach folder. Local files win; never overwrite."""
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if PACKS_DIR.is_dir():
        for src in sorted(PACKS_DIR.glob("*.md")):
            dest = folder / src.name
            if dest.exists():
                continue
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            written.append(dest)
    ensure_default_tone(folder)
    return written
