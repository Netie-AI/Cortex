"""Deterministic named-skill ingest: search, fetch, label, save.

The Manager should call this. If the model never starts (OpenVault 400/500),
the run path still finishes an ingest-shaped ask instead of stopping at
"Run failed".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_ADD = re.compile(
    r"(?:add|install|ingest|save|fetch|pull|find)\s+(?:the\s+)?"
    r"([A-Za-z0-9._-]{2,60})\s+skill",
    re.I,
)

_LABEL_CUES: tuple[tuple[str, str], ...] = (
    ("design-rules", "design"),
    ("motion", "motion"),
    ("frontend", "frontend"),
    ("clone-website", "clone"),
    ("3d", "3d"),
)


def extract_skill_name(text: str) -> str:
    raw = (text or "").strip()
    hit = _ADD.search(raw)
    if hit:
        return hit.group(1).strip("._-")
    return ""


def classify_labels(blob: str, *, name: str = "") -> list[str]:
    low = f"{name} {blob}".lower()
    out: list[str] = []
    for label, cue in _LABEL_CUES:
        if cue in low and label not in out:
            out.append(label)
    if not out:
        out.append("playbook")
    return out[:4]


def ingest_named_skill(query: str, folder: Path) -> dict[str, Any]:
    """Search GitHub/web, fetch a README, save a labeled Teach skill."""
    from CortexOS.crew.board import save_skill
    from CortexOS.crew import research

    name = extract_skill_name(query)
    if not name:
        bits = [t for t in re.findall(r"[A-Za-z0-9._-]{2,60}", query or "") if t.lower() not in {"add", "the", "into", "skill", "storage", "github"}]
        name = bits[0] if bits else "skill"
    search_q = f"{name} github skill"
    github = research.github_search(search_q, limit=8)
    web = research.web_search(search_q, max_results=6)
    source, snippet = _pick_source(name, github, web)
    body = ""
    if source:
        fetched = research.web_fetch(_raw_readme(source), max_chars=8000)
        if fetched.get("ok"):
            body = str(fetched.get("text") or "")
        if len(body) < 80:
            fetched = research.web_fetch(source, max_chars=8000)
            if fetched.get("ok"):
                body = str(fetched.get("text") or body)
    if not body.strip():
        body = (
            f"Ingested name {name}. Search did not return a readable README. "
            f"Re-run after a live GitHub/web hop. Query: {search_q}"
        )
    labels = classify_labels(body, name=name)
    playbook = _playbook(name, source, body)
    saved = save_skill(folder, name, playbook, labels=labels, source=source)
    return {
        "ok": True,
        "name": name,
        "source": source,
        "labels": saved.get("labels") or ",".join(labels),
        "path": saved.get("path") or "",
        "snippet": (snippet or body)[:400],
    }


def _pick_source(name: str, github: dict[str, Any], web: dict[str, Any]) -> tuple[str, str]:
    key = name.lower()
    for row in github.get("repos") or []:
        if not isinstance(row, dict):
            continue
        full = str(row.get("full_name") or "")
        url = str(row.get("url") or "")
        blob = f"{full} {url} {row.get('description') or ''}".lower()
        if key and key in blob and _public_http(url):
            return url, str(row.get("description") or full)
    for row in github.get("web") or web.get("results") or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        if "github.com" in url and key in url.lower() and _public_http(url):
            return url.split("?")[0], str(row.get("snippet") or row.get("title") or "")
    for row in web.get("results") or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        if _public_http(url):
            return url, str(row.get("snippet") or row.get("title") or "")
    return "", ""


def _public_http(url: str) -> bool:
    from CortexOS.crew.research import refuse_nonpublic_url

    return url.startswith("http") and not refuse_nonpublic_url(url)


def _raw_readme(url: str) -> str:
    raw = (url or "").rstrip("/")
    if "github.com" in raw and "/blob/" not in raw:
        # https://github.com/owner/repo -> raw README
        parts = raw.split("github.com/")[-1].split("/")
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
    if "/blob/" in raw:
        return raw.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    return raw


def _playbook(name: str, source: str, body: str) -> str:
    excerpt = "\n".join((body or "").splitlines()[:80]).strip()
    return (
        f"Skill {name}: follow these rules when the job matches.\n"
        f"Source: {source or '(search miss)'}\n\n"
        f"{excerpt}\n"
    )
