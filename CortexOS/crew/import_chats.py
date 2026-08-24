"""Import Grok/Rakazo/markdown chat dumps into a Crew space. Never reads AppData blobs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from CortexOS.crew.store import CrewStore

_HEADING = re.compile(r"^#{1,3}\s+(user|assistant|manager|agent|system|tool)\b[:\s]*", re.I)


def parse_export(raw: str) -> list[dict[str, str]]:
    text = (raw or "").strip()
    if not text:
        return []
    cursor_turns = _cursor_jsonl(text)
    if cursor_turns:
        return cursor_turns
    if text[0] in "{[":
        try:
            blob = json.loads(text)
        except ValueError:
            blob = None
        if isinstance(blob, dict):
            rows = blob.get("messages") or blob.get("thread") or blob.get("items") or []
            return [_norm(r) for r in rows if _norm(r)]
        if isinstance(blob, list):
            return [_norm(r) for r in blob if _norm(r)]
    lines = text.splitlines()
    if any(ln.strip().startswith("{") for ln in lines[:5]) and "\n{" in text:
        out: list[dict[str, str]] = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            n = _norm(row)
            if n:
                out.append(n)
        if out:
            return out
    return _markdown_turns(text)


def _cursor_jsonl(text: str) -> list[dict[str, str]]:
    """Cursor agent-transcript jsonl (role + message.content text parts)."""
    lines = text.splitlines()
    if not any('"message"' in ln and '"role"' in ln for ln in lines[:8]):
        return []
    turns: list[dict[str, str]] = []
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").lower()
        if role not in {"user", "assistant"}:
            continue
        msg = row.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
        body = "\n".join(p for p in parts if p).strip()
        if body:
            turns.append({"role": role, "content": body[:8000]})
    return turns


def _norm(row: Any) -> dict[str, str] | None:
    if isinstance(row, str) and row.strip():
        return {"role": "user", "content": row.strip()}
    if not isinstance(row, dict):
        return None
    role = str(row.get("role") or row.get("type") or row.get("from") or "user").lower()
    if role in {"bot", "manager", "assistant"}:
        role = "assistant"
    if role not in {"user", "assistant", "system", "agent", "tool"}:
        role = "user"
    content = row.get("content") or row.get("text") or row.get("message") or ""
    if isinstance(content, list):
        content = "\n".join(str(p.get("text") if isinstance(p, dict) else p) for p in content)
    content = str(content).strip()
    if not content:
        return None
    return {"role": role, "content": content}


def _markdown_turns(text: str) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    role = "user"
    buf: list[str] = []
    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            turns.append({"role": role, "content": body})
        buf.clear()

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            role = m.group(1).lower()
            if role == "manager":
                role = "assistant"
            rest = line[m.end() :].strip()
            if rest:
                buf.append(rest)
            continue
        buf.append(line)
    flush()
    return turns


def ingest(store: CrewStore, title: str, raw: str) -> dict[str, Any]:
    messages = parse_export(raw)
    space = store.create_space(title.strip() or "Imported chat")
    for msg in messages:
        store.add_message(space["id"], msg["role"], msg["content"], meta={"imported": True})
    return {"ok": True, "space": space, "count": len(messages)}


def parse_mail(raw: str) -> dict[str, str]:
    text = (raw or "").replace("\r\n", "\n")
    subject = ""
    sender = ""
    body_lines: list[str] = []
    headers = True
    for line in text.splitlines():
        if headers and line.lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip()
            continue
        if headers and line.lower().startswith("from:"):
            sender = line.split(":", 1)[1].strip()
            continue
        if headers and line == "":
            headers = False
            continue
        if headers and ":" not in line:
            headers = False
        if not headers:
            body_lines.append(line)
    body = "\n".join(body_lines).strip() or text.strip()
    return {"subject": subject or "(no subject)", "from": sender or "(unknown)", "body": body}


def ingest_mail(store: CrewStore, raw: str, filename: str = "mail") -> dict[str, Any]:
    parsed = parse_mail(raw)
    title = f"Mail: {parsed['subject']}"[:80]
    space = store.create_space(title)
    blob = f"From: {parsed['from']}\nSubject: {parsed['subject']}\n\n{parsed['body']}"
    store.add_message(space["id"], "user", blob, meta={"imported": True, "kind": "mail"})
    store.add_message(
        space["id"],
        "system",
        "Email ingest. Spawn Ticket and PR on this thread. Human remains the sender. "
        "Do not invent other inbox mail.",
        meta={"imported": True},
    )
    return {"ok": True, "space": space, "subject": parsed["subject"], "from": parsed["from"], "filename": filename}


_SKIP_PARTS = ("local storage", "leveldb", "indexeddb")
_EXPORT_SUFFIX = {".md", ".json", ".jsonl", ".txt"}


def _blocked(path: Path) -> bool:
    """True for Grok/browser profile blobs. Pytest Temp under AppData is allowed."""
    parts = [p.lower() for p in Path(path).parts]
    joined = "/".join(parts)
    if any(s in joined for s in _SKIP_PARTS):
        return True
    for i, part in enumerate(parts):
        if part in {"appdata", "application data"}:
            rest = parts[i + 1 :]
            if "roaming" in rest or "grok" in rest:
                return True
    return False


def discover_exports(roots: list[Path], *, limit: int = 40) -> list[dict[str, str]]:
    """Readable chat dumps only. Never walks Grok Bot AppData."""
    hits: list[dict[str, str]] = []
    for root in roots:
        if root is None:
            continue
        try:
            root = Path(root)
        except TypeError:
            continue
        if _blocked(root) or not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for path in children:
            if len(hits) >= limit:
                return hits
            if path.is_dir() or path.suffix.lower() not in _EXPORT_SUFFIX:
                continue
            if _blocked(path):
                continue
            hits.append({"path": str(path), "name": path.name, "kind": path.suffix.lower()})
    return hits


def ingest_readable(
    store: CrewStore, roots: list[Path], *, skills_dir: Path | None = None
) -> dict[str, Any]:
    files = discover_exports(roots)
    spaces: list[dict[str, Any]] = []
    skills: list[str] = []
    skipped = 0
    for item in files:
        path = Path(item["path"])
        if _blocked(path):
            skipped += 1
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")[:200_000]
        except OSError:
            skipped += 1
            continue
        result = ingest(store, path.stem[:80] or "Imported chat", raw)
        spaces.append({"id": result["space"]["id"], "title": result["space"]["title"], "count": result["count"]})
        if skills_dir is not None and ("Goal:" in raw or "Steps:" in raw):
            skills_dir.mkdir(parents=True, exist_ok=True)
            slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in path.stem)[:60]
            skill_path = skills_dir / f"{slug or 'imported'}.md"
            skill_path.write_text(raw[:8000], encoding="utf-8")
            skills.append(skill_path.name)
    return {
        "ok": True,
        "files": len(files),
        "spaces": spaces,
        "skills": skills,
        "skipped": skipped,
        "appdata_blocked": True,
    }
