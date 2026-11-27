"""Natural-language schedules — "every weekday morning" → a real next-run time.

Users say when they want things, they do not compute interval_seconds. This
module turns everyday phrasing into a small spec and computes the next
occurrence from it. Pure and deterministic: every function takes the reference
timestamp, so tests never depend on the wall clock.

Specs are one of:
    {"kind": "interval", "seconds": 900}
    {"kind": "daily",    "at_hour": 9, "at_minute": 0}
    {"kind": "weekly",   "weekdays": [0,1,2,3,4], "at_hour": 9, "at_minute": 0}

Weekdays follow ``datetime.weekday()`` — Monday is 0.

Cron is off unless ``CORTEX_ROUTINES_CRON=1``. When that flag is on, a spec may
also be ``{"kind": "cron", "expr": "30 9 * * *", "tz": "Asia/Singapore"}``.
Five fields, Sunday = 0 (or 7). A ``tz`` on a daily or weekly spec is read in
that zone. Flag off, both are ignored and the three specs above are unchanged.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

DEFAULT_HOUR = 9
DEFAULT_MINUTE = 0
MIN_INTERVAL_SECONDS = 60  # nothing may be scheduled tighter than a minute

WEEKDAY_NAMES: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

TIME_OF_DAY: dict[str, tuple[int, int]] = {
    "midnight": (0, 0),
    "morning": (9, 0),
    "noon": (12, 0),
    "midday": (12, 0),
    "afternoon": (14, 0),
    "evening": (18, 0),
    "night": (21, 0),
}

_UNIT_SECONDS = {
    "second": 1, "sec": 1, "s": 1,
    "minute": 60, "min": 60, "m": 60,
    "hour": 3600, "hr": 3600, "h": 3600,
    "day": 86400, "d": 86400,
    "week": 604800, "w": 604800,
}

_EVERY_N = re.compile(
    r"\bevery\s+(?:(\d+)\s*)?(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)\b"
)
_AT_TIME = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b")
_AT_24H = re.compile(r"\bat\s+(\d{1,2}):(\d{2})\b")
_WEEKDAY_WORD = re.compile(r"\b(weekdays?|weekends?|business\s+days?)\b")


def _norm_unit(word: str) -> str:
    word = word.rstrip("s")
    return {"secs": "sec", "mins": "min", "hrs": "hr"}.get(word, word)


def _parse_clock(text: str) -> tuple[tuple[int, int] | None, list[str]]:
    """Explicit clock time wins over vague time-of-day words."""
    matched: list[str] = []
    m = _AT_24H.search(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            matched.append(m.group(0))
            return (hour, minute), matched

    m = _AT_TIME.search(text)
    if m:
        hour = int(m.group(1)) % 12
        minute = int(m.group(2) or 0)
        if m.group(3) == "pm":
            hour += 12
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            matched.append(m.group(0))
            return (hour, minute), matched

    for word, hm in TIME_OF_DAY.items():
        if re.search(rf"\b{word}\b", text):
            matched.append(word)
            return hm, matched
    return None, matched


def parse_schedule(text: str) -> dict[str, Any]:
    """Return {spec, matched, matched_text}. ``matched`` is False when nothing
    in the text looked like a schedule and the caller got the safe default."""
    lowered = (text or "").lower()
    matched_text: list[str] = []

    clock, clock_matches = _parse_clock(lowered)
    matched_text.extend(clock_matches)
    hour, minute = clock if clock else (DEFAULT_HOUR, DEFAULT_MINUTE)

    weekdays: list[int] = []
    word_match = _WEEKDAY_WORD.search(lowered)
    if word_match:
        matched_text.append(word_match.group(0))
        token = word_match.group(1)
        weekdays = [5, 6] if token.startswith("weekend") else [0, 1, 2, 3, 4]
    else:
        for name, index in WEEKDAY_NAMES.items():
            if re.search(rf"\b{name}\b", lowered) and index not in weekdays:
                weekdays.append(index)
                matched_text.append(name)
        weekdays.sort()

    if weekdays:
        return {
            "spec": {"kind": "weekly", "weekdays": weekdays, "at_hour": hour, "at_minute": minute},
            "matched": True,
            "matched_text": matched_text,
        }

    every = _EVERY_N.search(lowered)
    if every:
        count = int(every.group(1) or 1)
        unit = _norm_unit(every.group(2))
        seconds = max(MIN_INTERVAL_SECONDS, count * _UNIT_SECONDS.get(unit, 3600))
        matched_text.append(every.group(0))
        # "every day at 5pm" is a daily clock schedule, not a 24h interval.
        if unit == "day" and count == 1 and clock:
            return {
                "spec": {"kind": "daily", "at_hour": hour, "at_minute": minute},
                "matched": True,
                "matched_text": matched_text,
            }
        return {
            "spec": {"kind": "interval", "seconds": seconds},
            "matched": True,
            "matched_text": matched_text,
        }

    for word, seconds in (("hourly", 3600), ("nightly", 0), ("daily", 0), ("weekly", 0)):
        if re.search(rf"\b{word}\b", lowered):
            matched_text.append(word)
            if word == "hourly":
                return {
                    "spec": {"kind": "interval", "seconds": seconds},
                    "matched": True,
                    "matched_text": matched_text,
                }
            if word == "weekly":
                return {
                    "spec": {
                        "kind": "weekly",
                        "weekdays": [0],
                        "at_hour": hour,
                        "at_minute": minute,
                    },
                    "matched": True,
                    "matched_text": matched_text,
                }
            night_hour = 21 if word == "nightly" and clock is None else hour
            return {
                "spec": {"kind": "daily", "at_hour": night_hour, "at_minute": minute},
                "matched": True,
                "matched_text": matched_text,
            }

    if clock:  # "summarize my inbox at 8am" — a time with no cadence means daily
        return {
            "spec": {"kind": "daily", "at_hour": hour, "at_minute": minute},
            "matched": True,
            "matched_text": matched_text,
        }

    return {
        "spec": {"kind": "daily", "at_hour": DEFAULT_HOUR, "at_minute": DEFAULT_MINUTE},
        "matched": False,
        "matched_text": [],
    }


def cron_enabled() -> bool:
    """True only for the exact value ``CORTEX_ROUTINES_CRON=1``."""
    return os.getenv("CORTEX_ROUTINES_CRON", "").strip() == "1"


class CronFields(NamedTuple):
    minute: frozenset[int]
    hour: frozenset[int]
    dom: frozenset[int]
    month: frozenset[int]
    dow: frozenset[int]
    dom_star: bool
    dow_star: bool


def valid_tz(name: object) -> str | None:
    """IANA zone name, or None when this host does not know it."""
    if name is None:
        return None
    text = str(name).strip()
    if not text:
        return None
    try:
        ZoneInfo(text)
    except Exception:
        return None
    return text


def _parse_cron_field(part: str, lo: int, hi: int) -> frozenset[int] | None:
    """One cron field. ``hi == 7`` is day-of-week, where 7 means Sunday."""
    span_hi = hi
    store_hi = 6 if hi == 7 else hi
    values: set[int] = set()
    for piece in part.split(","):
        piece = piece.strip()
        if not piece:
            return None
        step = 1
        base = piece
        if "/" in piece:
            base, step_text = piece.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                return None
            step = int(step_text)
            base = base.strip()
        if base == "*":
            start, end = lo, store_hi
        elif "-" in base:
            left, right = base.split("-", 1)
            if not (left.isdigit() and right.isdigit()):
                return None
            start, end = int(left), int(right)
            if start < lo or end > span_hi or start > end:
                return None
        else:
            if not base.isdigit():
                return None
            start = end = int(base)
            if start < lo or start > span_hi:
                return None
        for number in range(start, end + 1, step):
            stored = 0 if hi == 7 and number == 7 else number
            if stored < lo or stored > store_hi:
                return None
            values.add(stored)
    return frozenset(values) if values else None


def parse_cron(expr: str) -> CronFields | None:
    """Five-field cron: minute hour day-of-month month day-of-week.

    When both day-of-month and day-of-week are restricted (neither is ``*``),
    a time matches if either field matches. ``None`` means the expression is
    not five valid fields.
    """
    parts = str(expr or "").split()
    if len(parts) != 5:
        return None
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    parsed: list[frozenset[int]] = []
    for part, (lo, hi) in zip(parts, ranges, strict=True):
        field = _parse_cron_field(part, lo, hi)
        if field is None:
            return None
        parsed.append(field)
    return CronFields(
        minute=parsed[0],
        hour=parsed[1],
        dom=parsed[2],
        month=parsed[3],
        dow=parsed[4],
        dom_star=parts[2] == "*",
        dow_star=parts[4] == "*",
    )


def _cron_matches(moment: datetime, fields: CronFields) -> bool:
    if moment.minute not in fields.minute or moment.hour not in fields.hour:
        return False
    if moment.month not in fields.month:
        return False
    # datetime.weekday() is Monday=0. Cron day-of-week is Sunday=0.
    cron_dow = (moment.weekday() + 1) % 7
    dom_ok = moment.day in fields.dom
    dow_ok = cron_dow in fields.dow
    if fields.dom_star and fields.dow_star:
        return True
    if fields.dom_star:
        return dow_ok
    if fields.dow_star:
        return dom_ok
    return dom_ok or dow_ok


def _localize_wall(naive: datetime, tz: ZoneInfo) -> datetime | None:
    """First instant of this wall clock, or None if the zone skips that minute.

    A repeated hour (fall back) keeps fold 0, so the schedule fires once.
    """
    aware = naive.replace(tzinfo=tz)
    try:
        back = datetime.fromtimestamp(aware.timestamp(), tz).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None
    if back != naive:
        return None
    return aware


# Four years so 29 Feb is reachable. A cron that never matches must not turn
# into a daily fire: the caller gets a far-future instant instead.
_CRON_SEARCH_MINUTES = 366 * 4 * 24 * 60


def _next_cron(expr: str, after: float, tz_name: str) -> float:
    fields = parse_cron(expr)
    tz = ZoneInfo(tz_name or "UTC")
    if fields is None:
        return after + 86400.0
    local = datetime.fromtimestamp(after, tz)
    cursor = local.replace(second=0, microsecond=0, fold=0).replace(tzinfo=None)
    for _ in range(_CRON_SEARCH_MINUTES):
        cursor += timedelta(minutes=1)
        if not _cron_matches(cursor, fields):
            continue
        localized = _localize_wall(cursor, tz)
        if localized is None:
            continue
        stamp = localized.timestamp()
        if stamp > after:
            return stamp
    return after + (366 * 4 * 86400.0)


def _cron_expr_for_clock(norm: dict[str, Any]) -> str:
    minute = int(norm["at_minute"])
    hour = int(norm["at_hour"])
    if norm["kind"] == "weekly":
        dows = ",".join(str((int(day) + 1) % 7) for day in norm["weekdays"])
        return f"{minute} {hour} * * {dows}"
    return f"{minute} {hour} * * *"


def normalize_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce anything into a valid spec — a malformed one must never wedge tick."""
    spec = dict(spec or {})
    kind = str(spec.get("kind") or "daily")
    if cron_enabled() and kind == "cron":
        expr = " ".join(str(spec.get("expr") or "").split())
        if parse_cron(expr) is not None:
            return {"kind": "cron", "expr": expr, "tz": valid_tz(spec.get("tz")) or "UTC"}
        kind = "daily"
    if kind == "interval":
        seconds = int(spec.get("seconds") or 3600)
        result: dict[str, Any] = {
            "kind": "interval",
            "seconds": max(MIN_INTERVAL_SECONDS, seconds),
        }
    else:
        hour = int(spec.get("at_hour", DEFAULT_HOUR)) % 24
        minute = int(spec.get("at_minute", DEFAULT_MINUTE)) % 60
        if kind == "weekly":
            days = sorted({int(d) % 7 for d in (spec.get("weekdays") or [0])})
            result = {
                "kind": "weekly",
                "weekdays": days or [0],
                "at_hour": hour,
                "at_minute": minute,
            }
        else:
            result = {"kind": "daily", "at_hour": hour, "at_minute": minute}
    if cron_enabled():
        tz = valid_tz(spec.get("tz"))
        if tz:
            result = dict(result)
            result["tz"] = tz
    return result


def next_occurrence(spec: dict[str, Any] | None, after: float) -> float:
    """First run strictly after ``after`` (epoch seconds).

    Without a zone this is local time, the same interval/daily/weekly path as
    before. The cron and ``tz`` branches run only when ``normalize_spec`` kept
    them, and it keeps them only while ``CORTEX_ROUTINES_CRON=1``.
    """
    norm = normalize_spec(spec)
    if norm["kind"] == "interval":
        return after + norm["seconds"]
    if norm["kind"] == "cron":
        return _next_cron(norm["expr"], after, str(norm.get("tz") or "UTC"))
    if norm.get("tz"):
        return _next_cron(_cron_expr_for_clock(norm), after, str(norm["tz"]))

    hour, minute = norm["at_hour"], norm["at_minute"]
    allowed = norm["weekdays"] if norm["kind"] == "weekly" else list(range(7))
    base = time.localtime(after)
    for day_offset in range(0, 9):
        candidate = time.mktime(
            (
                base.tm_year, base.tm_mon, base.tm_mday + day_offset,
                hour, minute, 0, 0, 0, -1,
            )
        )
        if candidate > after and time.localtime(candidate).tm_wday in allowed:
            return candidate
    return after + 86400  # unreachable for valid specs; never return the past


def _approx_cron_seconds(expr: str) -> int:
    parsed = parse_cron(expr)
    if parsed is None:
        return 86400
    if len(parsed.minute) >= 60:
        return MIN_INTERVAL_SECONDS
    if len(parsed.minute) > 1 and len(parsed.hour) >= 24:
        return max(MIN_INTERVAL_SECONDS, int(3600 / len(parsed.minute)))
    if len(parsed.hour) >= 24 and parsed.dom_star and parsed.dow_star:
        return 3600
    return 86400


def approx_interval_seconds(spec: dict[str, Any] | None) -> int:
    """Legacy interval_seconds column stays populated so old readers still work."""
    norm = normalize_spec(spec)
    if norm["kind"] == "interval":
        return int(norm["seconds"])
    if norm["kind"] == "cron":
        return _approx_cron_seconds(norm["expr"])
    if norm["kind"] == "daily":
        return 86400
    days = len(norm["weekdays"]) or 1
    return int(604800 / days)


_ORDINAL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def describe(spec: dict[str, Any] | None) -> str:
    """Human sentence for the UI — never show a user raw seconds."""
    norm = normalize_spec(spec)
    if norm["kind"] == "cron":
        return f"Cron {norm['expr']} ({norm.get('tz') or 'UTC'})"
    if norm["kind"] == "interval":
        seconds = norm["seconds"]
        for unit_seconds, label in ((604800, "week"), (86400, "day"), (3600, "hour"), (60, "minute")):
            if seconds >= unit_seconds and seconds % unit_seconds == 0:
                count = seconds // unit_seconds
                return f"Every {label}" if count == 1 else f"Every {count} {label}s"
        return f"Every {seconds} seconds"

    clock = _format_clock(norm["at_hour"], norm["at_minute"])
    zone = f" ({norm['tz']})" if norm.get("tz") else ""
    if norm["kind"] == "daily":
        return f"Every day at {clock}{zone}"
    days = norm["weekdays"]
    if days == [0, 1, 2, 3, 4]:
        return f"Every weekday at {clock}{zone}"
    if days == [5, 6]:
        return f"Every weekend day at {clock}{zone}"
    names = ", ".join(_ORDINAL_DAYS[d] for d in days)
    return f"Every {names} at {clock}{zone}"


def _format_clock(hour: int, minute: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    display = hour % 12 or 12
    return f"{display}:{minute:02d} {suffix}"
