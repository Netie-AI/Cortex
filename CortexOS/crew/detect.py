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
from dataclasses import dataclass
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
    "Foundry": (
        "ontology",
        "foundry",
        "object type",
        "object types",
        "link types",
        "action types",
        "company ontology",
        "ontology-foundry",
        "digital twin",
        "aip parity",
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
        return {
            "pattern": self.pattern,
            "why": self.why,
            "capabilities": caps,
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
    return tuple(found)


def attached_skills(caps: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        out.append(name)

    # Company ontology/foundry jobs: ontology-foundry then build, even if PR
    # or Gate also matched and would otherwise copy build first.
    if "Foundry" in caps:
        _add("ontology-foundry")
        _add("build")
    for cap in caps:
        role = roles.by_name(cap)
        if role is None:
            continue
        for name in role.skills:
            _add(name)
    return tuple(out)


def plan(text: str) -> Detected:
    """Recommend a crew pattern for one user message.

    Prefer single-agent. Spawn only when the text has real capability
    boundaries or the operator asked to verify against explicit criteria.
    """
    raw = (text or "").strip()
    if not raw:
        return Detected(
            pattern="single_agent",
            why="empty message",
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
        lines.append("Default skills (auto-copied on spawn): " + ", ".join(skill_names))
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
