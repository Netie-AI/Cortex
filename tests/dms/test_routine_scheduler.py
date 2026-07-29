"""Routine scheduler + governor tests (data/engine/routines.db isolated per test)."""

from __future__ import annotations

import pytest

from CortexOS.execution import routine_scheduler as rs


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from CortexOS.execution import action_event, action_value

    monkeypatch.setattr(rs, "DB_PATH", tmp_path / "routines.db")
    # G2.4: every run now emits a trace and teaches the value table.
    monkeypatch.setattr(action_event, "DB_PATH", tmp_path / "action_events.db")
    monkeypatch.setattr(action_value, "DB_PATH", tmp_path / "action_value.db")
    rs.init()


def test_create_list_get_delete():
    routine = rs.create_routine("Daily brief", "summarize the day", interval_seconds=60)

    assert routine["preset"] == "minimal"
    assert routine["depth"] == "basic"
    assert rs.list_routines()[0]["id"] == routine["id"]

    assert rs.delete_routine(routine["id"]) is True
    assert rs.get_routine(routine["id"]) is None


def test_invalid_depth_falls_back_to_basic():
    routine = rs.create_routine("X", "y", depth="galactic")
    assert routine["depth"] == "basic"


@pytest.mark.asyncio
async def test_run_once_records_run_and_advances_schedule():
    routine = rs.create_routine("Echo", "hello routine", interval_seconds=3600)

    out = await rs.run_once(routine["id"])

    assert out["ok"] is True
    runs = rs.list_runs(routine["id"])
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"

    updated = rs.get_routine(routine["id"])
    assert updated["error_streak"] == 0
    assert updated["last_run_at"] is not None
    assert updated["next_run_at"] > updated["last_run_at"]


@pytest.mark.asyncio
async def test_tick_runs_due_then_waits():
    rs.create_routine("Due now", "hello", interval_seconds=3600)

    first = await rs.tick()
    assert len(first) == 1 and first[0]["ok"] is True

    second = await rs.tick()
    assert second == []  # next_run_at moved an hour out


@pytest.mark.asyncio
async def test_pause_skips_and_resume_restores():
    routine = rs.create_routine("Pausable", "hello", interval_seconds=0)
    rs.pause(routine["id"])

    assert await rs.tick() == []

    rs.resume(routine["id"])
    ran = await rs.tick()
    assert len(ran) == 1 and ran[0]["ok"] is True


@pytest.mark.asyncio
async def test_governor_pauses_on_error_streak():
    routine = rs.create_routine(
        "Broken", "hello", preset="langgraph", interval_seconds=0
    )  # honest adapter_unavailable → deterministic failure

    for _ in range(rs.GOVERNOR_ERROR_STREAK):
        await rs.tick()

    paused = rs.get_routine(routine["id"])
    assert paused["status"] == "paused"
    assert paused["paused_reason"].startswith("governor:error_streak")

    assert await rs.tick() == []  # governor keeps it parked


def test_governor_pauses_on_cost_cap():
    routine = rs.create_routine("Pricey", "hello", daily_cost_cap_myr=0.5)
    rs.update_routine(routine["id"], cost_today=1.0, cost_day=rs._today())

    action = rs.apply_governor(routine["id"])

    assert action == "paused:cost_cap"
    assert rs.get_routine(routine["id"])["paused_reason"] == "governor:cost_cap"


@pytest.mark.asyncio
async def test_cost_day_rollover_resets_budget():
    routine = rs.create_routine("Rollover", "hello", interval_seconds=0)
    rs.update_routine(routine["id"], cost_today=4.9, cost_day="2000-01-01")

    await rs.run_once(routine["id"])

    updated = rs.get_routine(routine["id"])
    assert updated["cost_day"] == rs._today()
    assert updated["cost_today"] < 4.9  # yesterday's spend didn't carry over


def test_scheduler_thread_start_stop():
    assert rs.start(poll_seconds=3600) is True
    assert rs.start(poll_seconds=3600) is False  # one per process
    rs.stop()


@pytest.mark.asyncio
async def test_lease_blocks_double_run_until_stale():
    import time as _time

    routine = rs.create_routine("Leased", "hello", interval_seconds=3600)
    rs.update_routine(routine["id"], status="running", running_since=_time.time())

    blocked = await rs.run_once(routine["id"])
    assert blocked["error"] == "already_running"

    rs.update_routine(
        routine["id"], running_since=_time.time() - rs.RUN_LEASE_SECONDS - 1
    )
    recovered = await rs.run_once(routine["id"])  # crashed run's lease is stale
    assert recovered["ok"] is True
    assert rs.get_routine(routine["id"])["status"] == "idle"


@pytest.mark.asyncio
async def test_timeout_bounds_a_hung_run(monkeypatch):
    import asyncio as _asyncio

    async def _hang(plan, body):
        await _asyncio.sleep(5)
        return {"ok": True}

    monkeypatch.setattr(rs, "execute_run_plan", _hang)
    routine = rs.create_routine("Hung", "x", timeout_seconds=0.05)

    out = await rs.run_once(routine["id"])

    assert out["ok"] is False
    assert "timeout" in out["error"]
    state = rs.get_routine(routine["id"])
    assert state["error_streak"] == 1
    assert state["status"] == "idle"  # never wedged in 'running'


@pytest.mark.asyncio
async def test_run_history_pruned(monkeypatch):
    monkeypatch.setattr(rs, "KEEP_RUNS_PER_ROUTINE", 3)
    routine = rs.create_routine("Chatty", "hello", interval_seconds=0)
    for _ in range(5):
        await rs.run_once(routine["id"])

    assert len(rs.list_runs(routine["id"], limit=50)) == 3


@pytest.mark.asyncio
async def test_global_budget_gates_tick(monkeypatch):
    routine = rs.create_routine("Spender", "hello", interval_seconds=0)
    monkeypatch.setattr(rs, "GLOBAL_DAILY_COST_CAP_MYR", 0.5)
    rs.update_routine(routine["id"], cost_today=1.0, cost_day=rs._today())

    assert rs.global_budget_state()["exhausted"] is True
    assert await rs.tick() == []  # engine-wide stop, even though the routine is due


def test_pause_all_resume_all_respects_governor():
    a = rs.create_routine("A", "x")
    b = rs.create_routine("B", "x")
    rs.pause(b["id"], "governor:error_streak:3")

    assert rs.pause_all() == 1  # only A was still unpaused
    assert rs.get_routine(a["id"])["status"] == "paused"

    assert rs.resume_all() == 1  # A resumes; governor-paused B stays parked
    assert rs.get_routine(a["id"])["status"] == "idle"
    assert rs.get_routine(b["id"])["status"] == "paused"


def test_schema_migration_adds_new_columns(tmp_path, monkeypatch):
    import sqlite3 as _sqlite3

    old_db = tmp_path / "old-routines.db"
    with _sqlite3.connect(old_db) as conn:
        conn.execute(
            """CREATE TABLE routines (
                 id TEXT PRIMARY KEY, name TEXT, prompt TEXT,
                 preset TEXT DEFAULT 'minimal', depth TEXT DEFAULT 'basic',
                 interval_seconds INTEGER DEFAULT 3600, enabled INTEGER DEFAULT 1,
                 status TEXT DEFAULT 'idle', paused_reason TEXT DEFAULT '',
                 error_streak INTEGER DEFAULT 0, cost_today REAL DEFAULT 0,
                 cost_day TEXT DEFAULT '', daily_cost_cap_myr REAL DEFAULT 5.0,
                 vars TEXT DEFAULT '{}', last_run_at REAL, next_run_at REAL,
                 created_at REAL)"""
        )
        conn.execute(
            "INSERT INTO routines (id, name, prompt, created_at) VALUES ('rt-old', 'Old', 'x', 1.0)"
        )
    monkeypatch.setattr(rs, "DB_PATH", old_db)

    rs.init()  # live engine's pre-hardening DB migrates in place

    migrated = rs.get_routine("rt-old")
    assert migrated is not None
    assert "timeout_seconds" in migrated
    assert "running_since" in migrated
