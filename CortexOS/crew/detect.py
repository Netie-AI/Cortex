"""Cheap task detect: pattern + capability templates, not a fixed roster.

distill: skill_distill/learned/multi_agent_coordination.md
distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md

Keyword match only. The Manager still calls spawn_agent; this module names
which capability prompts fit and which coordination pattern the engine already
owns (crew A2A, generator-verifier, gen_cfsm on the answer plane). It does not
start a third orchestrator and it does not revive LangGraph.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from CortexOS.crew import roles
from CortexOS.crew.estate import PRODUCTION_CAPS

# Word/phrase cues onto capability templates. Names are templates, not agents.
CUES: dict[str, tuple[str, ...]] = {
    "Ticket": (
        "ticket",
        "tickets",
        "bug",
        "complaint",
        "acceptance criteria",
        "file an issue",
        "github issue",
        "open an issue",
    ),
    "PRD": ("prd", "requirements doc", "product requirements", "write a spec"),
    "Epic": ("epic", "slice into epics", "sequence the work", "break the prd"),
    "Gate": (
        "gate",
        "invariant",
        "invariants",
        "lint-imports",
        "manifest refusal",
        "rls-proof",
        "before shipping",
        "production ready",
        "production essentials",
        "ship gate",
        "ship this",
    ),
    "Security": (
        "sql injection",
        "xss",
        "csrf",
        "rbac",
        "encryption at rest",
        "secret rotation",
        "gdpr",
        "hipaa",
        "soc 2",
        "soc2",
        "input validation",
    ),
    "Reliability": (
        "unit tests",
        "integration tests",
        "end-to-end",
        "load test",
        "circuit breaker",
        "graceful degradation",
        "retry logic",
        "edge case",
    ),
    "Infra": (
        "ci/cd",
        "infrastructure as code",
        "disaster recovery",
        "kubernetes",
        "docker",
        "cdn setup",
        "database migrations",
    ),
    "Architecture": (
        "connection pooling",
        "api versioning",
        "idempotency",
        "webhook handling",
        "background jobs",
        "sharding",
    ),
    "Observability": (
        "distributed tracing",
        "real user monitoring",
        "sentry",
        "vulnerability scanning",
        "cost optimization",
        "error tracking",
    ),
    "Surface": (
        "wcag",
        "accessibility",
        "internationalization",
        "i18n",
        "privacy policy",
        "feature flags",
        "a/b testing",
        "make a website",
        "build a website",
        "make a site",
        "landing page",
        "3d website",
        "3d site",
        "clone http",
        "clone https",
        "clone www",
        "clone a site",
        "clone the site",
        "clone this site",
    ),
    "Marketing": (
        "outbound",
        "founder voice",
        "sales copy",
        "outreach",
        "cold email",
        "whatsapp",
        "sound human",
        "chat reply",
        "linkedin",
        "connection note",
        "connect a few",
    ),
    "SEO": ("seo", "meta description", "search ranking", "on-page"),
    "Money": ("payback", "cost rm", "build vs buy", "cheaper alternative"),
    "Decision": ("decision", "options and evidence", "kill-criterion", "recommendation"),
    "PR": ("pull request", "draft a pr", "pr title", "open prs"),
    "Email": (
        "email",
        "imap",
        ".eml",
        "mailbox",
        "inbox mail",
        "who authorized",
        "who authorised",
        "authorization",
        "authorisation",
    ),
    "Connector": ("connector", "mcp", "openvault", "login", "api key", "imap app password"),
    "Browser": ("browser", "click the page", "computer use", "computer-use", "playwright"),
    "Skills": (
        "teach a skill",
        "find-skills",
        "find skills",
        "playbook",
        "distill",
        "learn voice",
        "learn speaking",
        "learn from this",
        "bad feedback",
        "customer feedback",
        "turn this into a skill",
        "skill storage",
        "add a skill",
        "ingest skill",
        "install skill",
        "save this skill",
        "github skill",
    ),
    "Routines": ("routine", "schedule", "cron", "plane beat", "watchdog beat"),
    "Watchdog": ("watchdog", "desk status", "what's seated", "seated vs unseated"),
}

_NO_SPAWN = (
    "do not spawn",
    "don't spawn",
    "do not spawn agents",
    "exactly the word",
)

_VERIFY = (
    "verify",
    "verifier",
    "fact-check",
    "fact check",
    "must be correct",
    "acceptance",
    "rubric",
    "gate this",
    "against invariants",
)

_SHIP = (
    "before shipping",
    "production ready",
    "production essentials",
    "ship gate",
    "ship this",
)


def _collapse_production_sweep(text: str, caps: tuple[str, ...]) -> tuple[str, ...]:
    """A production sweep is one Gate, not six specialists.

    One or two named domains stay (SQL injection, WCAG). Three or more
    production headings, or explicit ship-gate wording with a sweep,
    collapse to Gate so max_agents_per_space is not blown.
    """
    prod = tuple(c for c in caps if c in PRODUCTION_CAPS)
    others = tuple(c for c in caps if c not in PRODUCTION_CAPS)
    sweep = _hit(text, _SHIP) and len(prod) >= 2
    if sweep or len(prod) >= 3:
        if "Gate" in others:
            return others
        return ("Gate",) + others
    if _hit(text, _SHIP) and "Gate" not in caps:
        return ("Gate",) + caps
    return caps


@dataclass(frozen=True)
class Detected:
    pattern: str
    why: str
    capabilities: tuple[str, ...]
    spawn: bool
    verify: bool
    engine_path: str
    multi_agent_justified: bool

    def as_dict(self) -> dict[str, Any]:
        caps = []
        for name in self.capabilities:
            role = roles.by_name(name)
            caps.append(
                {
                    "name": name,
                    "blurb": role.blurb if role else "",
                    "icon": role.icon if role else name[:1],
                }
            )
        skills = list(attached_skills(self.capabilities))
        waves = wave_plan(self.capabilities) if self.spawn else ""
        return {
            "pattern": self.pattern,
            "why": self.why,
            "capabilities": caps,
            "skills": skills,
            "load_skill_now": bool(skills) and self.spawn,
            "wave_plan": waves,
            "spawn": self.spawn,
            "verify": self.verify,
            "engine_path": self.engine_path,
            "multi_agent_justified": self.multi_agent_justified,
        }


def _hit(text: str, cues: tuple[str, ...]) -> bool:
    low = text.lower()
    for cue in cues:
        c = cue.lower()
        if " " in c or "." in c:
            if c in low:
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(c)}(?![a-z0-9])", low):
            return True
    return False


def match_capabilities(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for role in roles.ROLES:
        cues = CUES.get(role.name, (role.name.lower(),))
        if _hit(text, cues):
            found.append(role.name)
    if _skill_ingest_ask(text) and "Skills" not in found:
        found.append("Skills")
    return tuple(found)


def _skill_ingest_ask(text: str) -> bool:
    """add impeccable skill / install X from github - verb plus the word skill."""
    low = (text or "").lower()
    if "skill" not in low:
        return False
    verbs = ("add", "ingest", "install", "save", "teach", "fetch", "find", "pull")
    return any(v in low for v in verbs)


def attached_skills(caps: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for cap in caps:
        role = roles.by_name(cap)
        if role is None:
            continue
        for name in role.skills:
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
    return tuple(out)


def plan(text: str) -> Detected:
    """Recommend a crew pattern for one user message.

    Prefer single-agent. Spawn only when the text has real capability
    boundaries or the operator asked to verify against explicit criteria.
    """
    raw = (text or "").strip()
    if not raw:
        # Idle empty is not a detected single_agent job (R-0011).
        return Detected(
            pattern="idle",
            why="idle empty. No user text.",
            capabilities=(),
            spawn=False,
            verify=False,
            engine_path="crew A2A; engine gen_cfsm stays on open-band analytics",
            multi_agent_justified=False,
        )

    if _hit(raw, _NO_SPAWN):
        return Detected(
            pattern="single_agent",
            why="operator forbade spawn; answer yourself",
            capabilities=(),
            spawn=False,
            verify=False,
            engine_path="crew A2A; engine gen_cfsm stays on open-band analytics",
            multi_agent_justified=False,
        )

    caps = match_capabilities(raw)
    caps = _collapse_production_sweep(raw, caps)
    verify = _hit(raw, _VERIFY)
    rec = None
    try:
        from CortexOS.execution.coordination_patterns import recommend_from_prompt

        rec = recommend_from_prompt(raw)
    except Exception:  # noqa: BLE001 - detect must never fail a turn
        rec = None

    multi = len(caps) >= 2 or (rec is not None and rec.multi_agent_justified and bool(caps))
    if verify and caps:
        pattern = "generator_verifier"
        why = "quality-critical with a matching capability; verifier needs explicit criteria"
        spawn = True
    elif multi:
        pattern = "orchestrator_subagent"
        why = "multiple capability boundaries; spawn job-named teammates, not catalog labels"
        spawn = True
    elif len(caps) == 1:
        pattern = "orchestrator_subagent"
        why = f"one capability template fits ({caps[0]}); spawn a job-named teammate if the work is not a one-line reply"
        spawn = True
    else:
        pattern = "single_agent"
        why = (rec.why if rec is not None else "no capability boundary; Manager answers")
        spawn = False

    engine_path = (
        rec.cortex_path
        if rec is not None
        else "crew A2A; engine gen_cfsm stays on open-band analytics"
    )
    return Detected(
        pattern=pattern,
        why=why,
        capabilities=caps,
        spawn=spawn,
        verify=verify,
        engine_path=engine_path,
        multi_agent_justified=multi or verify,
    )


def render(detected: Detected) -> str:
    """Directive prefix for the latest user turn. ASCII only."""
    lines = [
        "[detect]",
        f"pattern: {detected.pattern}",
        f"why: {detected.why}",
        f"engine: {detected.engine_path}",
    ]
    if not detected.spawn:
        lines.append("Do not call spawn_agent this turn. Answer yourself.")
        return "\n".join(lines)
    names = ", ".join(detected.capabilities) or "(none named; invent a job name)"
    lines.append(f"capability templates that fit: {names}")
    skill_names = attached_skills(detected.capabilities)
    if skill_names:
        listed = ", ".join(skill_names)
        lines.append("Default skills (auto-copied on spawn): " + listed)
        lines.append(
            "Matching playbooks are inlined below. Follow them this turn. "
            "load_skill only if a playbook is truncated or missing."
        )
    lines.append(
        "Spawn job-named teammates with spawn_agent (name=<this-job>, "
        "capability=<template>, brief=...). Do not ask the user to pick a chip. "
        "Share tools; pass allow_tools/deny_tools to restrict. OpenVault holds keys."
    )
    if detected.verify:
        lines.append(
            "Set verify=true and verify_criteria (explicit list). No criteria means "
            "do not rubber-stamp; skip the verifier."
        )
    lines.append("Crew A2A is the graph. Do not start LangGraph.")
    return "\n".join(lines)


_SLASH_SKILL = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9._-]{0,63})(?:\s|$)")


def slash_skill(text: str) -> str | None:
    """Leading /name from the operator, grok-bot /menu shape, original parse.

    Only the first token. Paths like /crew/health are not a skill name.
    """
    raw = (text or "").strip()
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    hit = _SLASH_SKILL.match(raw)
    if hit is None:
        return None
    return hit.group(1)


def wave_plan(capabilities: Sequence[str], *, max_parallel: int = 4) -> str:
    """Plan-only fan-out for this turn. Spawns nothing.

    DeepAgents launches independent subagents in one message. Crew already has
    dispatch.plan; leaving it behind plan_waves meant a multi-capability detect
    never used it. Independent templates land in wave 0 so the Manager can
    spawn them together this turn.
    """
    names = [str(n).strip() for n in capabilities if str(n).strip()]
    if len(names) < 2:
        return ""
    from CortexOS.crew import dispatch

    items = []
    for name in names:
        role = roles.by_name(name)
        items.append(
            dispatch.WorkItem(
                id=name,
                description=(role.blurb if role is not None else name),
            )
        )
    return dispatch.plan(items, max_parallel=max_parallel).render()


PLAYBOOK_BODY_CHARS = 1600
PLAYBOOK_MAX_SKILLS = 4
PLAYBOOK_TOTAL_CHARS = 4800


def playbooks(names: Sequence[str], folder: Path | None) -> str:
    """Inline matching skill bodies so this turn uses them, not hopes for load_skill.

    DeepAgents lists name+description and waits for a read. OpenWork searches then
    executes. Crew already told the model to load_skill this turn; a lazy call
    left the playbook unused. Cap each body and the block so a long skill cannot
    eat the Manager's second system message.
    """
    wanted = [n for n in names if str(n).strip()]
    if not wanted or folder is None:
        return ""
    from CortexOS.crew.board import read_skill

    chunks: list[str] = []
    used = 0
    leftover: list[str] = []
    for i, name in enumerate(wanted):
        if len(chunks) >= PLAYBOOK_MAX_SKILLS:
            leftover.extend(wanted[i:])
            break
        body = read_skill(folder, name).strip()
        if not body:
            leftover.append(name)
            continue
        if len(body) > PLAYBOOK_BODY_CHARS:
            body = (
                body[:PLAYBOOK_BODY_CHARS].rstrip()
                + "\n(truncated; load_skill for the rest)"
            )
        chunk = f"[playbook: {name}]\n{body}"
        if used + 2 + len(chunk) > PLAYBOOK_TOTAL_CHARS:
            leftover.append(name)
            continue
        chunks.append(chunk)
        used += 2 + len(chunk)
    if not chunks and not leftover:
        return ""
    parts = ["\n\n".join(chunks)] if chunks else []
    if leftover:
        parts.append("Not inlined; load_skill: " + ", ".join(leftover))
    return "\n".join(p for p in parts if p)
