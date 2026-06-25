"""Outbound send windows — quiet hours + Friday prayer (MVP §5.2)."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo


def now_in_zone(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def is_within_quiet_hours(
    now: datetime,
    *,
    quiet_start: time = time(22, 0),
    quiet_end: time = time(8, 0),
) -> bool:
    """True when local clock is in nightly quiet band (crosses midnight)."""
    current = now.time()
    return current >= quiet_start or current < quiet_end


def _normalize_religion(user_religion: str | None) -> str:
    if user_religion is None:
        return ""
    return user_religion.strip().lower()


def user_observes_friday_prayer(user_religion: str | None) -> bool:
    r = _normalize_religion(user_religion)
    return r in {"muslim", "islam", "islam_muslim"}


def should_pause_for_friday_prayer(now: datetime, user_religion: str | None) -> bool:
    if not user_observes_friday_prayer(user_religion):
        return False
    if now.weekday() != 4:
        return False
    cur = now.time()
    return time(12, 30) <= cur <= time(14, 30)


def is_sendable_now(user_tz: str, user_religion: str | None, message_urgency: str) -> bool:
    """
    Gates outbound DAG nodes. ``message_urgency`` uses ``urgent`` to bypass windows.
    """
    if message_urgency.strip().lower() == "urgent":
        return True
    try:
        now = now_in_zone(user_tz)
    except Exception:
        now = datetime.now(tz=ZoneInfo("Asia/Kuala_Lumpur"))
    if is_within_quiet_hours(now):
        return False
    if should_pause_for_friday_prayer(now, user_religion):
        return False
    return True
