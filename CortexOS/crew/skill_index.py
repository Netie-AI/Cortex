"""Skill discovery: what each skill is FOR, not just that it exists.

DeepAgents MIT pattern (skills loaded on demand) - the discovery half, written
as original Cortex code. ``board.list_skills`` already lists titles and
``load_skill`` already pulls one body; the gap this closes is between them. A
roster of bare titles does not tell the model which skill applies, so it loads
the wrong one or loads three and burns the context window it was trying to
save.

Three failures this module is built to prevent:
- an unbounded roster silently eating the prompt: ``render`` has a hard char
  ceiling and, when it trims, SAYS it trimmed and how many it dropped, because
  a quietly shortened list reads as "those skills do not exist";
- a malformed skill file disappearing: it is skipped AND recorded in
  ``parse_errors``, because a silently skipped skill and a missing one look
  identical to whoever is debugging;
- a name reaching the filesystem: ``resolve`` refuses anything that leaves the
  skills root, and an unknown name comes back as a refusal that names the near
  matches (KB R-0011: a refusal carries its reason, a silent one reads as a
  hang).

Pure over one directory tree. No I/O outside the given root.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

# The roster block is prompt text on every turn, so it is budgeted, not trusted
# to stay small. ~2400 chars is roughly 30 one-line entries.
ROSTER_MAX_CHARS = 2400
DESCRIPTION_MAX_CHARS = 140
NEAR_MATCHES = 5
MAX_FILE_BYTES = 512 * 1024
_FRONTMATTER_SCAN_LINES = 40


class SkillIndexError(ValueError):
    """A skill lookup was refused. The message is the reason; surface it."""


@dataclass(frozen=True)
class SkillEntry:
    """One indexed skill: what to call it, what it is for, where it lives."""

    name: str
    description: str
    path: Path
    rel: str
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillIndex:
    """Built once per scan; rendering and resolving never touch the disk again."""

    root: Path
    entries: tuple[SkillEntry, ...]
    parse_errors: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(e.name for e in self.entries)

    def get(self, name: str) -> SkillEntry | None:
        key = _key(name)
        if not key:
            return None
        for entry in self.entries:
            if _key(entry.name) == key or _key(entry.path.stem) == key:
                return entry
        return None

    def resolve(self, name: str) -> Path:
        """Path for ``name``, or a refusal that lists the near matches.

        Never returns a path outside ``root``: a skill name is model-supplied
        text, and "load_skill ../../../.env" must fail closed rather than read.
        """
        raw = (name or "").strip()
        if not raw:
            raise SkillIndexError("DENIED: no skill name given")
        if any(sep in raw for sep in ("/", "\\")) or raw in {".", ".."} or ":" in raw:
            raise SkillIndexError(
                f"DENIED: skill name '{raw}' looks like a path; skills are named, not addressed"
            )
        entry = self.get(raw)
        if entry is None:
            raise SkillIndexError(self._unknown(raw))
        resolved = entry.path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:  # pragma: no cover - build_index already jails
            raise SkillIndexError(
                f"DENIED: skill '{raw}' resolves outside the skills root"
            ) from exc
        return resolved

    def render(self, *, limit: int = ROSTER_MAX_CHARS) -> str:
        """The compact roster block for a prompt, under a hard char ceiling."""
        if not self.entries:
            head = "Skills: none indexed."
            return _with_errors(head, self.parse_errors, limit)
        lines = [_roster_line(e) for e in self.entries]
        header = f"Skills ({len(lines)}) - load_skill(name) pulls one body:"
        kept: list[str] = []
        used = len(header)
        for line in lines:
            note_room = len(_omitted_note(len(lines) - len(kept)))
            if used + 1 + len(line) + 1 + note_room > limit:
                break
            kept.append(line)
            used += 1 + len(line)
        omitted = len(lines) - len(kept)
        block = "\n".join([header, *kept])
        if omitted:
            block = f"{block}\n{_omitted_note(omitted)}" if kept else _omitted_note(omitted)
        return _with_errors(block, self.parse_errors, limit)

    def _unknown(self, raw: str) -> str:
        near = difflib.get_close_matches(
            _key(raw), [_key(n) for n in self.names], n=NEAR_MATCHES, cutoff=0.5
        )
        hits = [n for n in self.names if _key(n) in near]
        for n in self.names:
            if len(hits) >= NEAR_MATCHES:
                break
            if n not in hits and _key(raw) and _key(raw) in _key(n):
                hits.append(n)
        if hits:
            return f"DENIED: no skill named '{raw}'. Near matches: " + ", ".join(hits)
        if self.names:
            listed = ", ".join(self.names[:NEAR_MATCHES])
            extra = len(self.names) - NEAR_MATCHES
            more = f" (+{extra} more)" if extra > 0 else ""
            return f"DENIED: no skill named '{raw}'. Known skills: {listed}{more}"
        return f"DENIED: no skill named '{raw}'. No skills are indexed in {self.root.name}."


def build_index(root: Path) -> SkillIndex:
    """Scan ``root`` once. A bad file is recorded, never fatal, never silent."""
    base = Path(root).resolve()
    entries: list[SkillEntry] = []
    errors: list[str] = []
    seen: dict[str, str] = {}
    for path in _candidates(base):
        rel = path.relative_to(base).as_posix()
        try:
            name, description, labels = _parse(path, base)
        except _Malformed as exc:
            errors.append(f"{rel}: {exc}")
            continue
        key = _key(name)
        if key in seen:
            errors.append(f"{rel}: duplicate skill name '{name}' (already from {seen[key]})")
            continue
        seen[key] = rel
        entries.append(
            SkillEntry(name=name, description=description, path=path, rel=rel, labels=labels)
        )
    entries.sort(key=lambda e: (_key(e.name), e.rel))
    return SkillIndex(root=base, entries=tuple(entries), parse_errors=tuple(errors))


_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], SkillIndex]] = {}


def index_for(root: Path) -> SkillIndex:
    """Cached ``build_index``; re-scans only when the directory actually changed.

    The roster is rendered on every turn. Re-parsing every skill body each time
    is the kind of cost that shows up as latency nobody attributes to a prompt
    block.
    """
    base = Path(root).resolve()
    signature = _signature(base)
    hit = _CACHE.get(str(base))
    if hit is not None and hit[0] == signature:
        return hit[1]
    index = build_index(base)
    _CACHE[str(base)] = (signature, index)
    return index


def render_roster(root: Path, *, limit: int = ROSTER_MAX_CHARS) -> str:
    """Convenience for callers that only want the prompt block."""
    return index_for(root).render(limit=limit)


class _Malformed(ValueError):
    """Why one skill file could not be indexed. Ends up in parse_errors."""


def _candidates(base: Path) -> list[Path]:
    """``<root>/name.md`` plus ``<root>/name/SKILL.md``. Deterministic order."""
    if not base.is_dir():
        return []
    found: list[Path] = []
    try:
        children = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    for child in children:
        try:
            if child.is_file() and child.suffix.lower() == ".md":
                found.append(child)
            elif child.is_dir():
                nested = child / "SKILL.md"
                if nested.is_file():
                    found.append(nested)
        except OSError:
            continue
    return found


def _parse(path: Path, base: Path) -> tuple[str, str, tuple[str, ...]]:
    resolved = path.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise _Malformed("file resolves outside the skills root") from exc
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise _Malformed(f"unreadable ({exc.__class__.__name__})") from exc
    if size > MAX_FILE_BYTES:
        raise _Malformed(f"exceeds {MAX_FILE_BYTES} bytes")
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _Malformed(f"unreadable ({exc.__class__.__name__})") from exc
    if not text.strip():
        raise _Malformed("empty file")
    lines = text.splitlines()
    name = _default_name(path, base)
    labels: tuple[str, ...] = ()
    if lines and lines[0].strip() == "---":
        meta, body = _frontmatter(lines)
        declared = _clean_name(meta.get("name", ""))
        if declared:
            name = declared
        description = _clean(meta.get("description", "")) or _lead(body)
        labels = _parse_labels(meta.get("labels", ""))
    else:
        description = _lead(lines)
    return name, description, labels


def _frontmatter(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    meta: dict[str, str] = {}
    for i, line in enumerate(lines[1:_FRONTMATTER_SCAN_LINES + 1], start=1):
        if line.strip() == "---":
            return meta, lines[i + 1:]
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip().lower()] = value.strip().strip("\"'")
    raise _Malformed("frontmatter opened with --- and never closed")


def _lead(lines: list[str]) -> str:
    """First real sentence. A markdown heading counts once its hashes are gone."""
    for line in lines:
        stripped = line.strip().lstrip("#").strip()
        if stripped and stripped != "---":
            return _clean(stripped)
    return ""


def _clean(value: str) -> str:
    flat = " ".join((value or "").split())
    if len(flat) > DESCRIPTION_MAX_CHARS:
        return flat[: DESCRIPTION_MAX_CHARS - 3].rstrip() + "..."
    return flat


def _parse_labels(value: str) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for part in (value or "").replace(";", ",").split(","):
        slug = " ".join(part.split()).strip().lower().replace(" ", "-")
        slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in slug).strip("-_")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        out.append(slug[:40])
        if len(out) >= 8:
            break
    return tuple(out)


def _roster_line(entry: SkillEntry) -> str:
    tag = f" [{', '.join(entry.labels)}]" if entry.labels else ""
    return f"- {entry.name}{tag}: {entry.description or '(no description)'}"


def _clean_name(value: str) -> str:
    flat = " ".join((value or "").split())
    if any(sep in flat for sep in ("/", "\\", ":")) or flat in {".", ".."}:
        return ""
    return flat[:60]


def _default_name(path: Path, base: Path) -> str:
    if path.name.lower() == "skill.md" and path.parent != base:
        return path.parent.name
    return path.stem


def _key(name: str) -> str:
    return " ".join((name or "").split()).strip().lower()


def _omitted_note(omitted: int) -> str:
    return (
        f"(roster truncated: {omitted} more skill(s) omitted to stay inside the prompt budget;"
        " ask for the full list if none of the above fit)"
    )


def _with_errors(block: str, errors: tuple[str, ...], limit: int) -> str:
    if not errors:
        return block[:limit]
    shown = ", ".join(e.split(":")[0] for e in errors[:NEAR_MATCHES])
    more = f" (+{len(errors) - NEAR_MATCHES} more)" if len(errors) > NEAR_MATCHES else ""
    note = f"({len(errors)} skill file(s) skipped as unreadable: {shown}{more})"
    if len(block) + 1 + len(note) > limit:
        return block[:limit]
    return f"{block}\n{note}"


def _signature(base: Path) -> tuple[tuple[str, int, int], ...]:
    rows: list[tuple[str, int, int]] = []
    for path in _candidates(base):
        try:
            stat = path.stat()
        except OSError:
            rows.append((path.name, -1, -1))
            continue
        rows.append((path.relative_to(base).as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(rows))


def clear_cache() -> None:
    """Drop the scan cache. Tests and a Teach write that must be seen at once."""
    _CACHE.clear()


__all__ = [
    "DESCRIPTION_MAX_CHARS",
    "ROSTER_MAX_CHARS",
    "SkillEntry",
    "SkillIndex",
    "SkillIndexError",
    "build_index",
    "clear_cache",
    "index_for",
    "render_roster",
]
