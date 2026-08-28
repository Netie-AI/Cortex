"""One sentence in, a complete routine out — the user should never see a knob.

"Summarize my open PRs every weekday morning" has to be enough. The engine
picks the schedule, the architecture, the depth, the success check, the
timeout and the spend cap, and reports every guess it made in plain English so
the person can glance at a preview and hit Create.

Deterministic and offline: no model call is needed to draft a routine, so the
Routines page stays instant and free. The architecture choice is the one thing
that genuinely improves over time — it reads the racing scoreboard, and when
no family has a proven winner it stores ``auto`` and lets the first run race.
"""

from __future__ import annotations

import re
import time
from typing import Any

from CortexOS.execution import schedule_spec

AUTO_PRESET = "auto"

# Cheap by default. Nothing here upgrades a routine without a reason in words.
DEPTH_TIMEOUTS = {"basic": 120.0, "high": 300.0, "max": 600.0}
DEPTH_COST_CAPS = {"basic": 1.0, "high": 3.0, "max": 8.0}

_DEEP_WORDS = re.compile(
    r"\b(deep|deeply|thorough|thoroughly|comprehensive|exhaustive|in[- ]depth|research|investigate|audit)\b"
)
_MEDIUM_WORDS = re.compile(
    r"\b(summar\w+|report|analy[sz]e|analysis|review|digest|brief|compare|triage)\b"
)
_QUICK_WORDS = re.compile(r"\b(quick\w*|simple|just|only|brief\w*|check|ping|glance)\b")

_MUST_INCLUDE = re.compile(r"\b(?:must|should)\s+(?:include|contain|mention)\s+([\w\s\-]{2,40})")

_DAY_WORDS = (
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|mon|tue|tues|wed|thu|thurs|fri|sat|sun"
)
_TIME_WORDS = r"morning|afternoon|evening|night|noon|midday|midnight"
_UNIT_WORDS = r"seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?"

# "at" and "on" are only schedule words when a time follows them — otherwise
# they are ordinary English ("research **on** competitors", "look **at** the logs").
_SCHEDULE_NOISE = re.compile(
    rf"\b(?:every|each)\b"
    rf"|\b(?:at|on)\b(?=\s+(?:\d|{_TIME_WORDS}|{_DAY_WORDS}))"
    rf"|\b(?:weekdays?|weekends?|business\s+days?|hourly|daily|nightly|weekly)\b"
    rf"|\b(?:{_DAY_WORDS})\b"
    rf"|\b(?:{_TIME_WORDS})\b"
    rf"|\b\d{{1,2}}:\d{{2}}\s*(?:am|pm)?\b"
    rf"|\b\d{{1,2}}\s*(?:am|pm)\b"
    rf"|\b\d+\s+(?={_UNIT_WORDS}\b)"
    rf"|\b(?:{_UNIT_WORDS})\b",
    re.IGNORECASE,
)

_FILLER_PREFIX = re.compile(
    r"^(please\s+|can you\s+|could you\s+|i want (?:you )?to\s+|i'?d like (?:you )?to\s+|help me\s+)+",
    re.IGNORECASE,
)


def infer_depth(goal: str) -> tuple[str, str]:
    """Return (depth, why). Cheapest tier that plausibly does the job."""
    lowered = (goal or "").lower()
    if _DEEP_WORDS.search(lowered):
        return "max", "you asked for depth, so this runs the thorough pipeline"
    if _QUICK_WORDS.search(lowered):
        return "basic", "this looks like a quick check, so it runs the cheap path"
    if _MEDIUM_WORDS.search(lowered):
        return "high", "summarising needs a bit more work than a plain lookup"
    return "basic", "starting cheap — you can raise this later"


def infer_predicates(goal: str) -> list[dict[str, Any]]:
    """Every routine gets a real success check, even when nobody asked for one."""
    predicates: list[dict[str, Any]] = [{"type": "nonempty"}]
    must = _MUST_INCLUDE.search(goal or "")
    if must:
        value = must.group(1).strip().rstrip(".,")
        if value:
            predicates.append({"type": "contains", "value": value})
    return predicates


def suggest_name(goal: str, limit: int = 48) -> str:
    """A short title with the scheduling words stripped out."""
    text = _FILLER_PREFIX.sub("", (goal or "").strip())
    text = _SCHEDULE_NOISE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -,.")
    # Stripping schedule words can leave a dangling connector ("… notes on").
    text = re.sub(r"^(and|at|on|in|the)\b\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\b(and|at|on|in|of|for|by|the)$", "", text, flags=re.IGNORECASE).strip(" -,.")
    if not text:
        return "New routine"
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text[0].upper() + text[1:]


def _pick_preset(goal: str) -> tuple[str, str]:
    """Learned winner when the scoreboard is confident, else race on first run."""
    try:
        from CortexOS.execution import scoreboard

        scoreboard.init()
        gate = scoreboard.should_race(goal)
        if not gate["race"] and gate.get("winner"):
            return gate["winner"], (
                f"reusing the '{gate['winner']}' approach that already works "
                "for similar tasks"
            )
    except Exception:
        pass  # a cold or unavailable scoreboard must never block drafting
    return AUTO_PRESET, "the first run will try a few approaches and keep the best"


def compose(goal: str, *, now: float | None = None) -> dict[str, Any]:
    """Draft a complete routine from one sentence. Never raises on odd input."""
    goal = (goal or "").strip()
    parsed = schedule_spec.parse_schedule(goal)
    spec = parsed["spec"]
    depth, depth_why = infer_depth(goal)
    preset, preset_why = _pick_preset(goal)

    assumptions: list[str] = []
    if not parsed["matched"]:
        assumptions.append(
            f"You didn't say when, so I set it to {schedule_spec.describe(spec).lower()}."
        )
    assumptions.append(f"Effort: {depth} — {depth_why}.")
    assumptions.append(f"Approach: {preset_why}.")
    assumptions.append(
        f"I'll stop a run that takes over {int(DEPTH_TIMEOUTS[depth])} seconds, "
        f"and pause the routine if it spends more than RM{DEPTH_COST_CAPS[depth]:g} in a day."
    )
    assumptions.append("If it fails 3 times in a row I'll pause it and tell you.")

    return {
        "name": suggest_name(goal),
        "prompt": goal,
        "preset": preset,
        "depth": depth,
        "predicates": infer_predicates(goal),
        "schedule": spec,
        "schedule_text": schedule_spec.describe(spec),
        "interval_seconds": schedule_spec.approx_interval_seconds(spec),
        "next_run_at": schedule_spec.next_occurrence(spec, time.time() if now is None else now),
        "timeout_seconds": DEPTH_TIMEOUTS[depth],
        "daily_cost_cap_myr": DEPTH_COST_CAPS[depth],
        "assumptions": assumptions,
        "schedule_recognized": parsed["matched"],
    }


SUGGESTIONS: list[str] = [
    "Summarize my open PRs every weekday morning",
    "Check the site is up every 15 minutes",
    "Draft release notes every Friday at 5pm",
    "Deep research on our competitors every Monday morning",
]
