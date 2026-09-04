"""Slash commands and @mentions for Crew chat.

Slash maps onto skills, routines, and desk tools already in this tree.
Mentions resolve to a live teammate or a capability template. Delivery
goes through the A2A Switchboard, not a second inbox.

Netie-native: no Grok Bot / mybot renderer, no analog CSS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from CortexOS.crew import roles
from CortexOS.crew.board import PACKS_DIR, list_skills
from CortexOS.crew.routines import catalog as routine_catalog

_SLASH = re.compile(r"^/([A-Za-z][A-Za-z0-9_-]*)(?:\s+(.*))?$", re.DOTALL)
_MENTION = re.compile(r"(?:^|[\s])@([A-Za-z][A-Za-z0-9_-]{0,63})")

# Desk tools the runtime already owns. Aliases are extra slashes, not new actions.
_DESK: tuple[dict[str, Any], ...] = (
    {
        "slash": "desk",
        "aliases": ("desk_status",),
        "kind": "desk",
        "action": "desk_status",
        "title": "Desk status",
        "hint": "PRs, mail, connectors, Cursor key. Do not auto-merge.",
    },
    {
        "slash": "board",
        "aliases": ("tickets", "netie_board"),
        "kind": "desk",
        "action": "netie_board",
        "title": "Ticket board",
        "hint": "CLAIMS seated vs unseated. Ticket Runner seats writers.",
    },
    {
        "slash": "estate",
        "aliases": ("estate_status",),
        "kind": "desk",
        "action": "estate_status",
        "title": "Estate status",
        "hint": "Netie-AI repos and production domains.",
    },
    {
        "slash": "ship_gate",
        "aliases": ("gate",),
        "kind": "desk",
        "action": "ship_gate",
        "title": "Ship-gate",
        "hint": "Deterministic production gate. Arg: repo slug or all.",
    },
    {
        "slash": "done",
        "aliases": ("close_issue",),
        "kind": "desk",
        "action": "close_issue",
        "title": "Close issue (HITL)",
        "hint": "/done owner/repo#n | comment. Refuses SEATED claims. Never merges.",
    },
    {
        "slash": "fetch",
        "aliases": ("issues",),
        "kind": "desk",
        "action": "fetch_issues",
        "title": "Fetch open issues",
        "hint": "Open GitHub issues for CLAIMS repos. Marks SEATED. Control does not assign.",
    },
    {
        "slash": "assign",
        "aliases": ("give",),
        "kind": "desk",
        "action": "assign_issue",
        "title": "Assign teammate (local)",
        "hint": "/assign owner/repo#n | Name. Local goal bind. Refuses SEATED. No GitHub assignee.",
    },
)

_LIFE: tuple[dict[str, Any], ...] = (
    {
        "slash": "spawn",
        "aliases": ("hire",),
        "kind": "life",
        "action": "spawn",
        "title": "Spawn teammate",
        "hint": "/spawn name | brief | goal",
    },
    {
        "slash": "kill",
        "aliases": (),
        "kind": "life",
        "action": "kill",
        "title": "Kill teammate",
        "hint": "/kill name | reason",
    },
    {
        "slash": "stop",
        "aliases": (),
        "kind": "life",
        "action": "stop",
        "title": "Stop teammate",
        "hint": "/stop name  (parks idle/goal; can accept again)",
    },
    {
        "slash": "idle",
        "aliases": (),
        "kind": "life",
        "action": "idle",
        "title": "Park idle",
        "hint": "/idle name",
    },
    {
        "slash": "wait",
        "aliases": (),
        "kind": "life",
        "action": "wait",
        "title": "Park waiting",
        "hint": "/wait name",
    },
    {
        "slash": "goal",
        "aliases": (),
        "kind": "life",
        "action": "goal",
        "title": "Set goal mode",
        "hint": "/goal name | goal text",
    },
)

_MEMORY: tuple[dict[str, Any], ...] = (
    {
        "slash": "memory",
        "aliases": ("facts",),
        "kind": "memory",
        "action": "list",
        "title": "List facts.md",
        "hint": "Durable markdown facts for this space. Survives Clear chat.",
    },
    {
        "slash": "remember",
        "aliases": (),
        "kind": "memory",
        "action": "remember",
        "title": "Save a fact",
        "hint": "/remember name | description | body",
    },
    {
        "slash": "recall",
        "aliases": (),
        "kind": "memory",
        "action": "recall",
        "title": "Search facts",
        "hint": "/recall keyword",
    },
    {
        "slash": "forget",
        "aliases": (),
        "kind": "memory",
        "action": "forget",
        "title": "Delete a fact",
        "hint": "/forget name",
    },
)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:60] or "cmd"


def _public_cmd(
    *,
    slash: str,
    kind: str,
    action: str,
    title: str,
    hint: str,
    aliases: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "slash": slash,
        "kind": kind,
        "action": action,
        "title": title,
        "hint": hint,
        "aliases": list(aliases),
    }


def catalog(skills_dir: Path | None = None) -> list[dict[str, Any]]:
    """Real Crew commands: desk tools, routines, then taught/shipped skills."""
    out: list[dict[str, Any]] = []
    taken: set[str] = set()

    def take(slash: str) -> bool:
        key = slash.lower()
        if key in taken:
            return False
        taken.add(key)
        return True

    for row in _DESK:
        slash = str(row["slash"])
        if not take(slash):
            continue
        aliases = tuple(a for a in row["aliases"] if take(a))
        out.append(
            _public_cmd(
                slash=slash,
                kind="desk",
                action=str(row["action"]),
                title=str(row["title"]),
                hint=str(row["hint"]),
                aliases=aliases,
            )
        )

    for row in _LIFE:
        slash = str(row["slash"])
        if not take(slash):
            continue
        aliases = tuple(a for a in row["aliases"] if take(a))
        out.append(
            _public_cmd(
                slash=slash,
                kind="life",
                action=str(row["action"]),
                title=str(row["title"]),
                hint=str(row["hint"]),
                aliases=aliases,
            )
        )

    for row in _MEMORY:
        slash = str(row["slash"])
        if not take(slash):
            continue
        aliases = tuple(a for a in row["aliases"] if take(a))
        out.append(
            _public_cmd(
                slash=slash,
                kind="memory",
                action=str(row["action"]),
                title=str(row["title"]),
                hint=str(row["hint"]),
                aliases=aliases,
            )
        )

    for row in routine_catalog():
        name = str(row.get("name") or "")
        slash = _slug(name)
        if not take(slash):
            continue
        out.append(
            _public_cmd(
                slash=slash,
                kind="routine",
                action=name,
                title=name,
                hint=str(row.get("instruction") or row.get("when") or "standing routine"),
            )
        )

    seen_skills: set[str] = set()
    folders = [p for p in (skills_dir, PACKS_DIR) if p is not None]
    for folder in folders:
        for skill in list_skills(folder):
            title = str(skill.get("title") or "")
            slash = _slug(title)
            if slash in seen_skills or not take(slash):
                continue
            seen_skills.add(slash)
            out.append(
                _public_cmd(
                    slash=slash,
                    kind="skill",
                    action=title,
                    title=title,
                    hint=str(skill.get("head") or "taught skill"),
                )
            )
    return out


def filter_commands(rows: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    needle = (prefix or "").strip().lstrip("/").lower()
    if not needle:
        return list(rows)
    hits: list[dict[str, Any]] = []
    for row in rows:
        aliases = [str(a).lower() for a in (row.get("aliases") or [])]
        blob = " ".join(
            [
                str(row.get("slash") or ""),
                str(row.get("title") or ""),
                str(row.get("action") or ""),
                *aliases,
            ]
        ).lower()
        if str(row.get("slash") or "").lower().startswith(needle) or any(
            a.startswith(needle) for a in aliases
        ):
            hits.append(row)
        elif needle in blob:
            hits.append(row)
    return hits


def mention_targets(agents: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Live teammates first, then capability templates the Manager may spawn."""
    out: list[dict[str, Any]] = []
    seen = set()

    def add(name: str, kind: str, hint: str, agent_id: str | None = None) -> None:
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        row: dict[str, Any] = {"name": name, "kind": kind, "hint": hint}
        if agent_id:
            row["id"] = agent_id
        out.append(row)

    add("Manager", "teammate", "Coordinates the crew")
    for agent in agents or []:
        name = str(agent.get("name") or "").strip()
        if not name:
            continue
        hint = str(agent.get("capability") or agent.get("status") or "teammate")
        add(name, "teammate", hint, str(agent.get("id") or "") or None)
    for role in roles.catalog():
        add(str(role["name"]), "role", str(role.get("blurb") or "capability template"))
    return out


def filter_mentions(rows: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    needle = (prefix or "").strip().lstrip("@").lower()
    if not needle:
        return list(rows)
    return [
        row
        for row in rows
        if str(row.get("name") or "").lower().startswith(needle)
        or needle in str(row.get("hint") or "").lower()
    ]


@dataclass(frozen=True)
class Mention:
    raw: str
    name: str
    kind: str  # teammate | role | unknown
    agent_id: str | None = None

    def public(self) -> dict[str, Any]:
        row: dict[str, Any] = {"raw": self.raw, "name": self.name, "kind": self.kind}
        if self.agent_id:
            row["id"] = self.agent_id
        return row


@dataclass(frozen=True)
class Parsed:
    text: str
    command: dict[str, Any] | None
    rest: str
    mentions: tuple[Mention, ...]

    def public(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "command": self.command,
            "rest": self.rest,
            "mentions": [m.public() for m in self.mentions],
        }


def parse_mentions(text: str) -> tuple[str, ...]:
    return tuple(m.group(1) for m in _MENTION.finditer(text or ""))


def resolve_mentions(
    names: tuple[str, ...],
    agents: list[dict[str, Any]] | None = None,
) -> tuple[Mention, ...]:
    by_agent = {str(a.get("name") or "").lower(): a for a in (agents or []) if a.get("name")}
    out: list[Mention] = []
    seen: set[str] = set()
    for raw in names:
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        agent = by_agent.get(key)
        if agent is not None:
            out.append(
                Mention(
                    raw=raw,
                    name=str(agent["name"]),
                    kind="teammate",
                    agent_id=str(agent.get("id") or "") or None,
                )
            )
            continue
        role = roles.by_name(raw)
        if role is not None:
            out.append(Mention(raw=raw, name=role.name, kind="role"))
            continue
        out.append(Mention(raw=raw, name=raw, kind="unknown"))
    return tuple(out)


def match_command(slash: str, skills_dir: Path | None = None) -> dict[str, Any] | None:
    key = (slash or "").strip().lstrip("/").lower()
    if not key:
        return None
    for row in catalog(skills_dir):
        aliases = {str(a).lower() for a in (row.get("aliases") or [])}
        if str(row.get("slash") or "").lower() == key or key in aliases:
            return row
    return None


def parse(
    text: str,
    skills_dir: Path | None = None,
    agents: list[dict[str, Any]] | None = None,
) -> Parsed:
    raw = text or ""
    command = None
    rest = raw
    matched = _SLASH.match(raw.strip())
    if matched:
        hit = match_command(matched.group(1), skills_dir)
        if hit is not None:
            command = hit
            rest = (matched.group(2) or "").strip()
    mentions = resolve_mentions(parse_mentions(raw), agents=agents)
    return Parsed(text=raw, command=command, rest=rest, mentions=mentions)
