"""Netie spine roster for Cortex Crew.

This is the in-engine contract for PRD / Epic / Ticket Runner and the rest of
FLEET.md. Prompts load from the deployed Claude agent files. We do not spawn
one agent per GitHub issue. Ticket Runner seats; it does not execute unless
this session is the seated writer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_CLAUDE_AGENTS = Path.home() / ".claude" / "agents"
_NETIE_AGENTS = Path(r"D:\Netie\Internal\Agents")
_PROMPT_CAP = 7000

_BAKED: dict[str, str] = {
    "prd-agent": (
        "You are the PRD Agent. Nearness first: measure how near the product is "
        "to the delivered app, including landing. Do not invent a percentage. "
        "Slice by irreversibility, not value. Never create tickets (Epic does). "
        "Never implement. Never widen a PRD without founder sign-off. "
        "Unrouted feature requests stop here. Hand off to Epic via send_message."
    ),
    "epic-agent": (
        "You are the Epic Agent. Turn an epic into tickets with Problem, "
        "Acceptance (WHEN/THE SYSTEM SHALL on the customer artifact), Why not a "
        "one-liner, Step, Agent prompt. Completeness is from code+tests, not "
        "checkboxes. Never close tickets. Never skip Depends-on. "
        "Hand Agent prompts to Ticket Runner via send_message."
    ),
    "ticket-runner": (
        "You are the Ticket Runner manager, not a swarm. Survey GitHub + holds. "
        "Seat every READY ticket in parallel on existing repo writers. Do not "
        "spawn one agent per issue. Do not execute unless this session is the "
        "seated writer. GATE FAIL = do not seat. Cortex = new unused branch only. "
        "Skip Cortex#42. Do not attach to Cortex PRs #41 #4 #43 #44. "
        "dms #61 stays HELD. Unrouted features go to PRD Agent via send_message."
    ),
    "pr-bot": (
        "You are PR Bot. Red PRs, one writer per branch, BRANCH_HOLD on duplicate "
        "ticket keys. Do not infinite-spawn. Do not mint a Space grant (Cortex#42). "
        "Do not merge named holds. MERGEABLE is not permission."
    ),
    "verify": (
        "You are Verify Agent. Reproduce on a different run than the implementer "
        "(R-0003). Do not close. Do not same-run verify. Report pass/fail with "
        "the command you ran."
    ),
    "gating": (
        "You are Gating Agent. CI floors only (MIN_TESTS, C2, R-0007). Do not "
        "write product code. Do not sit a writer. BRANCH_HOLD when a second "
        "writer would attach."
    ),
    "decision": (
        "You are Decision Agent. Founder cards only. Do not merge. Do not "
        "implement. Do not invent routine merge cards."
    ),
    "grok": (
        "You are Grok judgement on this laptop, not a clone of Grok Bot. "
        "Read D:\\Netie\\Internal\\Agents\\RUNTIME.md and GROK_SYNC.md. "
        "When Grok is usage-capped, verify and judge here; do not uninstall "
        "Grok; do not spawn infinite cloud agents; do not pip-install Hermes "
        "as a Grok clone. Ticket Runner still seats existing writers. "
        "Do not reverse-engineer %APPDATA%\\Grok Bot blobs."
    ),
}


def _strip_frontmatter(text: str) -> str:
    raw = text.replace("\r\n", "\n")
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            raw = parts[2]
    return raw.strip()


def _load(slug: str, rel: str | None) -> str:
    baked = _BAKED[slug]
    if not rel:
        return baked
    path = Path(rel)
    if not path.is_file():
        return baked
    body = _strip_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    if not body:
        return baked
    return body[:_PROMPT_CAP]


def spine() -> list[dict[str, Any]]:
    """Contract the UI and /crew/fleet expose. Prompts may be long; never is short."""
    prd = _CLAUDE_AGENTS / "prd-agent.md"
    epic = _CLAUDE_AGENTS / "epic-agent.md"
    ticket = _CLAUDE_AGENTS / "ticket-runner.md"
    return [
        {
            "slug": "ticket-runner",
            "name": "Ticket Runner",
            "emoji": "🎫",
            "color": "#7dd3a8",
            "does": "survey and seat READY tickets on existing writers",
            "never": "spawn one agent per issue; execute unless seated writer",
            "system_prompt": _load("ticket-runner", str(ticket)),
        },
        {
            "slug": "prd-agent",
            "name": "PRD Agent",
            "emoji": "📋",
            "color": "#8ab4f8",
            "does": "product nearness, slice PRD, route feedback",
            "never": "create tickets; implement; widen a PRD",
            "system_prompt": _load("prd-agent", str(prd)),
        },
        {
            "slug": "epic-agent",
            "name": "Epic Agent",
            "emoji": "📚",
            "color": "#c4b5fd",
            "does": "tickets + completeness from code",
            "never": "close tickets; trust checkboxes",
            "system_prompt": _load("epic-agent", str(epic)),
        },
        {
            "slug": "pr-bot",
            "name": "PR Bot",
            "emoji": "🔀",
            "color": "#f0a35e",
            "does": "one writer per branch, red PRs",
            "never": "merge holds; mint Space grants",
            "system_prompt": _load("pr-bot", None),
        },
        {
            "slug": "verify",
            "name": "Verify",
            "emoji": "🧪",
            "color": "#5eead4",
            "does": "different-run live check",
            "never": "close; same-run verify",
            "system_prompt": _load("verify", None),
        },
        {
            "slug": "gating",
            "name": "Gating",
            "emoji": "🚧",
            "color": "#fbbf24",
            "does": "CI floors",
            "never": "write product code; seat a writer",
            "system_prompt": _load("gating", None),
        },
        {
            "slug": "decision",
            "name": "Decision",
            "emoji": "⚖️",
            "color": "#fb7185",
            "does": "founder decision cards",
            "never": "merge; implement",
            "system_prompt": _load("decision", None),
        },
        {
            "slug": "grok",
            "name": "Grok",
            "emoji": "🐺",
            "color": "#e98ad2",
            "does": "judgement and verify while Grok Bot is capped or shared",
            "never": "clone Grok Bot; spawn a cloud agent per issue",
            "system_prompt": _load("grok", None),
        },
    ]


def starter() -> list[dict[str, Any]]:
    return [
        {
            "name": a["name"],
            "emoji": a["emoji"],
            "color": a["color"],
            "system_prompt": a["system_prompt"],
        }
        for a in spine()
    ]


def public_contract() -> list[dict[str, Any]]:
    return [
        {k: a[k] for k in ("slug", "name", "emoji", "does", "never")}
        for a in spine()
    ]


def netie_agents_dir() -> Path:
    return _NETIE_AGENTS
