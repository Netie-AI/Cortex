"""Scale and Build over tickets: seat existing writers, then verify by a named test.

Scale = seat existing writers, not one agent per issue (FLEET law;
``docs/subagents_findings/2026-08-24_overnight-watchdog-tickets.md``).
``/scale`` pairs ready fetched issues with teammates that are alive, parked
idle and not already bound. It spawns a job-named teammate only while the
space cap still leaves room for that writer AND its verifier. Everything else
is queued with a visible reason, never dropped silently (R-0011).

Build = skill ``build`` (Ponytail ladder) plus the existing generator-verifier
pass. The verify criteria name the exact command. The verifier is a different
run (R-0003) and must not paint green unless the command's real output is
pasted.

Local binds only. CLAIMS.json and GitHub assignees are never written. This is
not a second orchestrator: every seat goes through the same ``/assign`` bind,
A2A brief, and run that the operator already drives by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from CortexOS.crew import github as github_mod
from CortexOS.crew import life

#: Named in ``skill_packs/build.md`` ("Verify default for crew code"). A test
#: asserts the pack and this constant agree so they cannot drift silently.
DEFAULT_VERIFY_CMD = "python -m pytest tests/test_crew -q"
BUILD_SKILL = "build"
DEFAULT_SCALE_LIMIT = 3
MAX_SCALE_LIMIT = 20
#: One verifier row lands after each build worker finishes; it counts against
#: ``max_agents_per_space`` like any teammate, so a seat reserves its slot now.
VERIFIER_SLOTS = 1

LAW_BUILD = (
    "Local bind. Skill build copied in. Verifier runs after the worker finishes "
    "and fails closed without pasted output. Did not write CLAIMS.json. "
    "Did not set a GitHub assignee."
)
LAW_SCALE = (
    "Scale = seat existing writers, not one agent per issue. Local binds only. "
    "Did not write CLAIMS.json. Did not set a GitHub assignee."
)

REASON_CAP = "cap reached"
REASON_LIMIT = "over limit"
REASON_BOUND = "bound to"


def job_name(spec: str) -> str:
    """``owner/repo#n`` -> ``repo-n``. A job name for THIS ticket, not a catalog label."""
    parsed = github_mod.parse_issue_spec(spec)
    if parsed is None:
        return ""
    _owner, repo, number = parsed
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", repo.lower()).strip("-") or "ticket"
    return f"{slug}-{number}"[:64]


def parse_build_rest(rest: str) -> tuple[str, str, str]:
    """``/build owner/repo#n | Name | verify cmd`` -> (spec, name, cmd).

    Name and command are optional: the name defaults to :func:`job_name` at
    bind time and the command to :data:`DEFAULT_VERIFY_CMD`.
    """
    bits = [p.strip() for p in (rest or "").split("|")]
    spec = bits[0] if bits else ""
    name = bits[1] if len(bits) > 1 else ""
    cmd = "|".join(bits[2:]).strip() if len(bits) > 2 else ""
    return spec, name, (cmd or DEFAULT_VERIFY_CMD)


def parse_scale_rest(rest: str) -> tuple[int, list[str]]:
    """``/scale [n|all] [| Name, Name]`` -> (limit, preferred existing writers).

    ``/scale Scout, Gate`` (no number) keeps the default limit and names
    the writers to reuse.
    """
    bits = [p.strip() for p in (rest or "").split("|")]
    head = bits[0] if bits else ""
    names_blob = ",".join(bits[1:]) if len(bits) > 1 else ""
    limit = DEFAULT_SCALE_LIMIT
    if head:
        token = head.split()[0].rstrip(",")
        tail = head[len(head.split()[0]) :].strip()
        if token.isdigit():
            limit = max(1, min(MAX_SCALE_LIMIT, int(token)))
            if tail:
                names_blob = tail + ("," + names_blob if names_blob else "")
        elif token.lower() == "all":
            limit = MAX_SCALE_LIMIT
            if tail:
                names_blob = tail + ("," + names_blob if names_blob else "")
        else:
            names_blob = head + ("," + names_blob if names_blob else "")
    names: list[str] = []
    for raw in names_blob.split(","):
        name = raw.strip()
        if name and name.lower() not in {n.lower() for n in names}:
            names.append(name)
    return limit, names


def build_goal(canon: str, title: str, body: str, verify_cmd: str) -> str:
    """Goal text a build teammate keeps across chat clear (mode=goal)."""
    goal = (
        f"{canon}: {title}. Build this issue. Follow skill build: smallest change "
        "that works, Ponytail ladder, Cortex invariants. "
        f"Named verify command: {verify_cmd}. Paste its real output; do not claim "
        "green unless it ran. Close with request_close (HITL) or /done after verify. "
        "Do not steal SEATED seats. Do not merge PRs."
    )
    if body:
        goal = f"{goal}\n\n{body[:2000]}"
    return goal


def build_criteria(verify_cmd: str) -> list[str]:
    """Explicit pass/fail checks for the verifier. No criteria = no verifier."""
    return [
        f"The named verify command ran: {verify_cmd}",
        "Its real output is pasted, not summarised; no green claim without it",
        "Smallest change that works; no git add -A; no weakened refusal; "
        "no hand-edited contract spec",
        "SEATED seats untouched; no PR merged; close only via request_close HITL",
    ]


def ready_tickets(data_dir: Path, *, bound_specs: set[str] | None = None) -> list[dict[str, Any]]:
    """Fetched open issues that are ready: owner/repo#n, not SEATED, not bound."""
    taken = {github_mod.canonical_spec(s) for s in (bound_specs or set()) if s}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in github_mod.remembered_issues(data_dir):
        spec = github_mod.canonical_spec(str(row.get("spec") or ""))
        if not spec or spec in seen or spec in taken:
            continue
        if github_mod.parse_issue_spec(spec) is None:
            continue
        if row.get("seated") or github_mod.seated_claim(spec) is not None:
            continue
        seen.add(spec)
        out.append({"spec": spec, "title": str(row.get("title") or spec)})
    return out


@dataclass(frozen=True)
class Seat:
    spec: str
    title: str
    writer: str
    reused: bool  # existing teammate reused vs job-named spawn

    def public(self) -> dict[str, Any]:
        return {
            "spec": self.spec,
            "title": self.title,
            "writer": self.writer,
            "reused": self.reused,
        }


@dataclass
class ScalePlan:
    cap: int
    rows: int
    total: int
    limit: int
    seats: list[Seat] = field(default_factory=list)
    queued: list[tuple[str, str]] = field(default_factory=list)  # (spec, reason)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (writer, reason)

    @property
    def reused(self) -> int:
        return sum(1 for s in self.seats if s.reused)

    @property
    def spawned(self) -> int:
        return sum(1 for s in self.seats if not s.reused)

    def public(self) -> dict[str, Any]:
        return {
            "cap": self.cap,
            "rows": self.rows,
            "total": self.total,
            "limit": self.limit,
            "seats": [s.public() for s in self.seats],
            "queued": [{"spec": spec, "reason": why} for spec, why in self.queued],
            "skipped": [{"writer": who, "reason": why} for who, why in self.skipped],
            "law": LAW_SCALE,
        }


def writer_block(row: dict[str, Any], bound: dict[str, str], *, named: bool) -> str:
    """Why this teammate cannot take a ticket now. Empty string means it can."""
    name = str(row.get("name") or "")
    if name == "Manager":
        return "Manager never writes"
    if name.endswith("-verify"):
        # Generator-verifier rows carry the Gate role and one worker's criteria.
        return "verifier row, not a writer"
    status = life.canonical_status(str(row.get("status") or ""))
    if not life.is_alive(row) or status == life.STATUS_STOPPED:
        why = str(row.get("stop_reason") or "").strip()
        return f"stopped ({why})" if why else "stopped"
    if status == life.STATUS_FAILED:
        return "failed"
    if life.is_busy(row):
        return "busy"
    spec = bound.get(name.lower())
    if spec:
        return f"{REASON_BOUND} {spec}"
    if not named and status == life.STATUS_WAITING:
        return "parked waiting (name it to reuse)"
    if not named and status == life.STATUS_GOAL:
        return "goal mode (name it to reuse)"
    return ""


def plan_scale(
    tickets: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    *,
    cap: int,
    limit: int = DEFAULT_SCALE_LIMIT,
    names: list[str] | None = None,
    bound: dict[str, str] | None = None,
) -> ScalePlan:
    """Pair ready tickets with writers. Pure: no store, no spawn, no gh.

    Existing writers seat first. A job-named teammate is spawned only while
    ``cap`` leaves room for the writer and its verifier. ``bound`` maps a
    teammate name to the ticket it already holds.
    """
    plan = ScalePlan(cap=int(cap), rows=len(agents), total=len(tickets), limit=int(limit))
    bound_lower = {str(k).lower(): str(v) for k, v in (bound or {}).items() if str(k).strip()}
    by_name = {str(a.get("name") or "").lower(): a for a in agents if a.get("name")}
    writers: list[str] = []
    wanted = [n for n in (names or []) if str(n).strip()]
    if wanted:
        for raw in wanted:
            row = by_name.get(str(raw).lower())
            if row is None:
                plan.skipped.append((str(raw), "not in this space"))
                continue
            why = writer_block(row, bound_lower, named=True)
            if why:
                plan.skipped.append((str(row["name"]), why))
                continue
            if str(row["name"]) not in writers:
                writers.append(str(row["name"]))
    else:
        for row in agents:
            why = writer_block(row, bound_lower, named=False)
            if why:
                if why != "Manager never writes":
                    plan.skipped.append((str(row.get("name") or ""), why))
                continue
            writers.append(str(row["name"]))

    budget = plan.cap - plan.rows
    held = set(bound_lower.values())
    for index, ticket in enumerate(tickets):
        spec = github_mod.canonical_spec(str(ticket.get("spec") or ""))
        title = str(ticket.get("title") or spec)
        if not spec:
            continue
        if index >= plan.limit:
            plan.queued.append((spec, f"{REASON_LIMIT} {plan.limit}"))
            continue
        if spec in held:
            plan.queued.append((spec, "already bound"))
            continue
        if writers:
            if budget < VERIFIER_SLOTS:
                plan.queued.append((spec, f"{REASON_CAP} ({plan.cap} rows; verifier slot)"))
                continue
            writer = writers.pop(0)
            budget -= VERIFIER_SLOTS
            plan.seats.append(Seat(spec=spec, title=title, writer=writer, reused=True))
            continue
        name = job_name(spec)
        existing = by_name.get(name.lower())
        if existing is not None:
            why = writer_block(existing, bound_lower, named=True)
            if why:
                plan.queued.append((spec, f"{existing['name']} {why}"))
                continue
            if budget < VERIFIER_SLOTS:
                plan.queued.append((spec, f"{REASON_CAP} ({plan.cap} rows; verifier slot)"))
                continue
            budget -= VERIFIER_SLOTS
            plan.seats.append(
                Seat(spec=spec, title=title, writer=str(existing["name"]), reused=True)
            )
            continue
        cost = 1 + VERIFIER_SLOTS
        if budget < cost:
            plan.queued.append((spec, f"{REASON_CAP} ({plan.cap} rows; writer + verifier slots)"))
            continue
        budget -= cost
        plan.seats.append(Seat(spec=spec, title=title, writer=name, reused=False))
    return plan


def render_scale(plan: ScalePlan, outcomes: list[tuple[Seat, bool, str]]) -> str:
    """Operator-visible report. Counts only binds that actually succeeded."""
    seated = [seat for seat, ok, _text in outcomes if ok]
    reused = sum(1 for s in seated if s.reused)
    spawned = len(seated) - reused
    lines = [
        f"Scaled {len(seated)}/{plan.total} ready tickets: reused {reused} existing, "
        f"spawned {spawned} job-named (cap {plan.cap}, {plan.rows} rows before). "
        f"{LAW_SCALE}"
    ]
    for seat, ok, text in outcomes:
        how = "existing" if seat.reused else "spawned"
        mark = "" if ok else "FAILED "
        lines.append(f"- {mark}{seat.spec} -> {seat.writer} ({how}): {text}")
    for spec, why in plan.queued:
        lines.append(f"- queued {spec}: {why}")
    for who, why in plan.skipped:
        lines.append(f"- skipped {who}: {why}")
    return "\n".join(lines)
