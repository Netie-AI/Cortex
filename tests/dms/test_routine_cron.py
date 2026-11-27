"""Cron, time zone, and retry backoff for routines. Flag CORTEX_ROUTINES_CRON=1.

The clock in these tests is an argument, not the wall clock. Flag off, the
interval / daily / weekly path is the one the older tests already lock.
"""

from __future__ import annotations

import inspect
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from CortexOS.execution import routine_scheduler as rs
from CortexOS.execution import schedule_spec


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from CortexOS.execution import action_event, action_value

    monkeypatch.delenv("CORTEX_ROUTINES_CRON", raising=False)
    monkeypatch.setattr(rs, "DB_PATH", tmp_path / "routines.db")
    monkeypatch.setattr(action_event, "DB_PATH", tmp_path / "action_events.db")
    monkeypatch.setattr(action_value, "DB_PATH", tmp_path / "action_value.db")
    rs.init()


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_ROUTINES_CRON", "1")


async def _ok_plan(plan, body):
    return {"ok": True, "output": "ok"}


async def _fail_plan(plan, body):
    return {"ok": False, "error": "boom", "output": None}


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _poll_at(due: float, origin: float, poll: float) -> float:
    if due <= origin:
        return origin
    steps = math.ceil((due - origin) / poll - 1e-9)
    clock = origin + steps * poll
    if clock < due:
        clock += poll
    return clock


def test_flag_off_leaves_interval_daily_weekly_unchanged():
    now = 1_800_000_000.0
    assert schedule_spec.cron_enabled() is False
    assert schedule_spec.next_occurrence({"kind": "interval", "seconds": 900}, now) == now + 900
    assert schedule_spec.normalize_spec({"kind": "interval", "seconds": 10}) == {
        "kind": "interval",
        "seconds": 60,
    }
    daily = schedule_spec.normalize_spec(
        {"kind": "cron", "expr": "30 9 * * *", "tz": "Asia/Singapore"}
    )
    assert daily == {"kind": "daily", "at_hour": 9, "at_minute": 0}
    assert "tz" not in daily
    assert rs.default_poll_seconds() == rs.DEFAULT_POLL_SECONDS


def test_flag_off_weekday_sentence_unchanged():
    spec = {"kind": "weekly", "weekdays": [0, 1, 2, 3, 4], "at_hour": 9, "at_minute": 0}
    assert schedule_spec.describe(spec) == "Every weekday at 9:00 AM"


@pytest.mark.asyncio
async def test_flag_off_overdue_interval_still_runs_without_retry(monkeypatch):
    calls = {"n": 0}

    async def _count(plan, body):
        calls["n"] += 1
        return {"ok": False, "error": "boom", "output": None}

    monkeypatch.setattr(rs, "execute_run_plan", _count)
    routine = rs.create_routine(
        "Plain",
        "hello",
        interval_seconds=3600,
        on_missed="skip",
        retry_max=5,
        retry_base_seconds=10,
    )
    now = 2_000_000_000.0
    rs.update_routine(routine["id"], next_run_at=now - 10_000, error_streak=0)
    ran = await rs.tick(now=now)

    assert calls["n"] == 1
    assert ran[0]["ok"] is False
    assert ran[0].get("status") != "skipped"
    state = rs.get_routine(routine["id"])
    assert state["error_streak"] == 1
    assert state["status"] == "idle"
    assert state["next_run_at"] == pytest.approx(now + 3600)
    assert len(rs.list_runs(routine["id"])) == 1


def test_parse_cron_fields():
    _enable_direct = schedule_spec.parse_cron
    fields = _enable_direct("*/15 0-2 1,15 * 7")
    assert fields is not None
    assert fields.minute == frozenset({0, 15, 30, 45})
    assert fields.hour == frozenset({0, 1, 2})
    assert fields.dom == frozenset({1, 15})
    assert fields.dow == frozenset({0})
    assert schedule_spec.parse_cron("not cron") is None
    assert schedule_spec.parse_cron("* * *") is None
    assert schedule_spec.valid_tz("Asia/Singapore") == "Asia/Singapore"
    assert schedule_spec.valid_tz("Not/AZone") is None


def test_dom_or_dow_when_both_restricted(monkeypatch):
    _enable(monkeypatch)
    spec = {"kind": "cron", "expr": "0 0 15 * 1", "tz": "UTC"}
    # 2026-07-01 is a Wednesday. Next match is Monday 6 July, not the 15th.
    start = datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()
    first = schedule_spec.next_occurrence(spec, start)
    wall = datetime.fromtimestamp(first, timezone.utc)
    assert (wall.month, wall.day, wall.hour) == (7, 6, 0)
    assert wall.weekday() == 0

    # The 15th is a Wednesday, so it matches on the day-of-month side.
    before_fifteenth = datetime(2026, 7, 14, 12, tzinfo=timezone.utc).timestamp()
    second = schedule_spec.next_occurrence(spec, before_fifteenth)
    wall = datetime.fromtimestamp(second, timezone.utc)
    assert (wall.month, wall.day) == (7, 15)
    assert wall.weekday() == 2


def test_singapore_cron_wall_time(monkeypatch):
    _enable(monkeypatch)
    tz = ZoneInfo("Asia/Singapore")
    spec = {"kind": "cron", "expr": "30 9 * * *", "tz": "Asia/Singapore"}
    after = datetime(2026, 6, 15, 9, 0, tzinfo=tz).timestamp()
    nxt = schedule_spec.next_occurrence(spec, after)
    wall = datetime.fromtimestamp(nxt, tz)
    assert (wall.year, wall.month, wall.day, wall.hour, wall.minute) == (2026, 6, 15, 9, 30)
    assert wall.utcoffset() == timedelta(hours=8)
    utc = datetime.fromtimestamp(nxt, timezone.utc)
    assert (utc.hour, utc.minute) == (1, 30)
    assert schedule_spec.describe(spec) == "Cron 30 9 * * * (Asia/Singapore)"


def test_new_york_dst_transition(monkeypatch):
    _enable(monkeypatch)
    tz = ZoneInfo("America/New_York")
    spec = {"kind": "cron", "expr": "30 9 * * *", "tz": "America/New_York"}
    before = datetime(2026, 3, 7, 0, 0, tzinfo=tz).timestamp()
    saturday = schedule_spec.next_occurrence(spec, before)
    wall = datetime.fromtimestamp(saturday, tz)
    assert (wall.month, wall.day, wall.hour, wall.minute) == (3, 7, 9, 30)
    assert wall.utcoffset() == timedelta(hours=-5)

    sunday = schedule_spec.next_occurrence(spec, saturday)
    wall = datetime.fromtimestamp(sunday, tz)
    assert (wall.month, wall.day, wall.hour, wall.minute) == (3, 8, 9, 30)
    assert wall.utcoffset() == timedelta(hours=-4)

    # 02:30 does not exist on the spring-forward morning. Next real 02:30 is the 9th.
    gap_after = datetime(2026, 3, 8, 1, 0, tzinfo=tz).timestamp()
    gap = schedule_spec.next_occurrence(
        {"kind": "cron", "expr": "30 2 * * *", "tz": "America/New_York"},
        gap_after,
    )
    gap_wall = datetime.fromtimestamp(gap, tz)
    assert (gap_wall.month, gap_wall.day, gap_wall.hour, gap_wall.minute) == (3, 9, 2, 30)
    assert gap_wall.utcoffset() == timedelta(hours=-4)

    # Fall back: 01:30 happens twice. Fire the first one, then the next day.
    fold_after = datetime(2026, 11, 1, 0, 30, tzinfo=tz).timestamp()
    fold_spec = {"kind": "cron", "expr": "30 1 * * *", "tz": "America/New_York"}
    first = schedule_spec.next_occurrence(fold_spec, fold_after)
    first_wall = datetime.fromtimestamp(first, tz)
    assert (first_wall.month, first_wall.day, first_wall.hour, first_wall.minute) == (11, 1, 1, 30)
    assert first_wall.utcoffset() == timedelta(hours=-4)
    second = schedule_spec.next_occurrence(fold_spec, first)
    second_wall = datetime.fromtimestamp(second, tz)
    assert (second_wall.month, second_wall.day, second_wall.hour, second_wall.minute) == (11, 2, 1, 30)


@pytest.mark.asyncio
async def test_p95_lateness_at_most_one_second(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(rs, "execute_run_plan", _ok_plan)
    poll = rs.default_poll_seconds()
    assert poll == rs.CRON_POLL_SECONDS
    assert poll <= 1.0

    routine = rs.create_routine(
        "Pulse",
        "hello",
        schedule={"kind": "cron", "expr": "* * * * *", "tz": "UTC"},
    )
    due = 1_800_000_060.0
    origin = due - 60.0 + 0.3
    rs.update_routine(routine["id"], next_run_at=due, retry_used=0)
    lateness: list[float] = []
    for _ in range(100):
        state = rs.get_routine(routine["id"])
        scheduled = float(state["next_run_at"])
        clock = _poll_at(scheduled, origin, poll)
        assert 0 <= clock - scheduled <= poll + 1e-6
        ran = await rs.tick(now=clock)
        assert len(ran) == 1 and ran[0]["ok"] is True
        late = clock - float(ran[0]["scheduled_for"])
        lateness.append(late)
    assert len(lateness) == 100
    assert _p95(lateness) <= 1.0
    assert _p95(lateness) > 0.05
    assert max(lateness) <= 1.0


@pytest.mark.asyncio
async def test_retries_back_off_then_governor_pauses(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(rs, "execute_run_plan", _fail_plan)
    routine = rs.create_routine(
        "Retry",
        "hello",
        interval_seconds=3600,
        retry_max=2,
        retry_base_seconds=2,
    )
    t = 5_000_000_000.0
    rs.update_routine(routine["id"], next_run_at=t, error_streak=0, retry_used=0)
    for cycle in range(rs.GOVERNOR_ERROR_STREAK):
        for retry_index in range(2):
            out = await rs.run_once(routine["id"], now=t)
            assert out["ok"] is False
            state = rs.get_routine(routine["id"])
            assert state["status"] != "paused"
            assert state["error_streak"] == cycle
            delay = 2.0 * (2**retry_index)
            assert state["next_run_at"] == pytest.approx(t + delay)
            assert await rs.tick(now=t + delay - 0.01) == []
            t = float(state["next_run_at"])
        out = await rs.run_once(routine["id"], now=t)
        assert out["ok"] is False
        state = rs.get_routine(routine["id"])
        assert state["error_streak"] == cycle + 1
        if cycle + 1 < rs.GOVERNOR_ERROR_STREAK:
            assert state["status"] == "idle"
            assert state["next_run_at"] == pytest.approx(t + 3600)
            t = float(state["next_run_at"])
        else:
            assert state["status"] == "paused"
            assert str(state["paused_reason"]).startswith("governor:error_streak")
    runs = list(reversed(rs.list_runs(routine["id"], limit=50)))
    assert [row["attempt"] for row in runs] == [1, 2, 3] * rs.GOVERNOR_ERROR_STREAK
    assert {row["status"] for row in runs} == {"error"}
    assert await rs.tick(now=t + 10_000) == []


@pytest.mark.asyncio
async def test_missed_fire_skip_and_catch_up(monkeypatch):
    _enable(monkeypatch)
    calls = {"n": 0}

    async def _count(plan, body):
        calls["n"] += 1
        return {"ok": True, "output": "ok"}

    monkeypatch.setattr(rs, "execute_run_plan", _count)
    now = 1_700_000_000.0
    skipped = rs.create_routine(
        "Skip",
        "hello",
        schedule={"kind": "cron", "expr": "* * * * *", "tz": "UTC"},
        on_missed="skip",
    )
    rs.update_routine(skipped["id"], next_run_at=now - 3600)
    ran = await rs.tick(now=now)
    assert calls["n"] == 0
    assert ran[0]["status"] == "skipped"
    state = rs.get_routine(skipped["id"])
    assert state["next_run_at"] > now
    assert rs.list_runs(skipped["id"])[0]["status"] == "skipped"
    # The skipped slot is not a dead routine: the next future slot still fires.
    await rs.tick(now=float(state["next_run_at"]))
    assert calls["n"] == 1

    calls["n"] = 0
    caught = rs.create_routine(
        "Catch",
        "hello",
        schedule={"kind": "cron", "expr": "* * * * *", "tz": "UTC"},
        on_missed="catch_up",
    )
    rs.update_routine(caught["id"], next_run_at=now - 3600)
    ran = await rs.tick(now=now)
    assert calls["n"] == 1
    assert ran[0]["ok"] is True
    state = rs.get_routine(caught["id"])
    assert now < float(state["next_run_at"]) <= now + 60
    assert await rs.tick(now=now) == []
    assert calls["n"] == 1

    # Inside the grace window a skip policy still fires. That is lateness, not a miss.
    calls["n"] = 0
    near = rs.create_routine(
        "Near",
        "hello",
        interval_seconds=3600,
        on_missed="skip",
    )
    rs.update_routine(near["id"], next_run_at=now - 0.5)
    ran = await rs.tick(now=now)
    assert calls["n"] == 1
    assert ran[0].get("status") != "skipped"


@pytest.mark.asyncio
async def test_run_history_redacts_secrets_and_keeps_fifty(monkeypatch):
    _enable(monkeypatch)
    secret = "sk-fakefakefake12"

    async def _leak(plan, body):
        return {"ok": True, "output": f"token={secret} plus {secret}"}

    monkeypatch.setattr(rs, "execute_run_plan", _leak)
    routine = rs.create_routine("Leak", "hello", interval_seconds=60)
    await rs.run_once(routine["id"])
    blob = rs.list_runs(routine["id"])[0]["output"] or ""
    assert secret not in blob
    assert "<redacted>" in blob

    async def _ok(plan, body):
        return {"ok": True, "output": "plain"}

    monkeypatch.setattr(rs, "execute_run_plan", _ok)
    chatty = rs.create_routine("Chatty", "hello", interval_seconds=60)
    for _ in range(55):
        await rs.run_once(chatty["id"])
    assert len(rs.list_runs(chatty["id"], limit=50)) == 50
    assert len(rs.list_runs(chatty["id"], limit=100)) == 50


def test_scheduler_does_not_send_mail_or_chat():
    source = inspect.getsource(rs)
    assert "smtplib" not in source
    assert "send_digest" not in source


def test_api_flag_off_ignores_cron_in_vars(monkeypatch):
    app = FastAPI()
    from CortexOS.api.routine_routes import register_routine_routes

    register_routine_routes(app)
    client = TestClient(app)
    created = client.post(
        "/api/routines",
        json={
            "prompt": "hello every day at 5pm",
            "vars": {"cron": "15 8 * * *", "tz": "Asia/Singapore", "retry_max": 4},
        },
    )
    assert created.status_code == 200
    schedule = created.json()["routine"]["schedule"]
    assert schedule["kind"] == "daily"
    assert schedule["at_hour"] == 17
    assert "tz" not in schedule
    assert created.json()["routine"]["retry_max"] == 0


@pytest.mark.asyncio
async def test_api_cron_round_trip_and_retained_runs(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(rs, "execute_run_plan", _ok_plan)
    app = FastAPI()
    from CortexOS.api.routine_routes import register_routine_routes

    register_routine_routes(app)
    client = TestClient(app)
    bad = client.post("/api/routines", json={"prompt": "hello", "vars": {"cron": "not a cron"}})
    assert bad.status_code == 400
    bad_zone = client.post(
        "/api/routines",
        json={"prompt": "hello", "vars": {"cron": "30 9 * * *", "tz": "Not/AZone"}},
    )
    assert bad_zone.status_code == 400

    created = client.post(
        "/api/routines",
        json={
            "name": "SG",
            "prompt": "hello",
            "vars": {
                "cron": "30 9 * * *",
                "tz": "Asia/Singapore",
                "retry_max": 2,
                "retry_base_seconds": 1,
                "on_missed": "skip",
            },
        },
    )
    assert created.status_code == 200
    routine = created.json()["routine"]
    assert routine["schedule"]["kind"] == "cron"
    assert routine["schedule"]["expr"] == "30 9 * * *"
    assert routine["schedule"]["tz"] == "Asia/Singapore"
    assert routine["retry_max"] == 2
    assert routine["on_missed"] == "skip"
    assert "Asia/Singapore" in routine["schedule_text"]

    for _ in range(55):
        await rs.run_once(routine["id"])
    listed = client.get(f"/api/routines/{routine['id']}/runs?limit=50")
    assert listed.status_code == 200
    runs = listed.json()["runs"]
    assert len(runs) == rs.KEEP_RUNS_PER_ROUTINE
    assert "attempt" in runs[0]
