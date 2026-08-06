"""A routine that can never succeed is paused on the first run, not the Nth.

Measured on this machine before the fix, from `data/engine/action_events.db`:

    routine_langgraph  langgraph  failed  327

327 consecutive runs of a routine pinned to a preset whose adapter returns a
hardcoded 501. The governor's error-streak pause exists to tell a *flaky*
routine from a broken one — a question that does not arise when the run is
impossible. Waiting out the streak spent 327 scheduler slots re-learning a fact
available before the first run.
"""

from __future__ import annotations

import asyncio

import pytest

from CortexOS.execution import routine_scheduler as rs


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "DB_PATH", tmp_path / "routines.db")
    rs.init()
    return tmp_path


def _make(preset: str) -> str:
    r = rs.create_routine(
        name=f"probe-{preset}",
        prompt="do the thing",
        preset=preset,
        interval_seconds=60,
    )
    return r["id"]


def test_routine_on_an_unimplemented_preset_pauses_after_one_run(store) -> None:
    """The whole point: one failure, then parked with a reason."""
    rid = _make("langgraph")

    out = asyncio.run(rs.run_once(rid))

    assert out["ok"] is False
    assert out["governor"] == "paused:permanent"

    routine = rs.get_routine(rid)
    assert routine["status"] == "paused"
    assert "permanent" in routine["paused_reason"]


def test_the_pause_reason_names_the_cause(store) -> None:
    """An operator has to see why without reading the scheduler source."""
    rid = _make("langchain")

    asyncio.run(rs.run_once(rid))

    assert "preset_unavailable" in rs.get_routine(rid)["paused_reason"]


def test_a_paused_routine_does_not_run_again(store) -> None:
    """The loop is actually stopped, not merely labelled."""
    rid = _make("langgraph")
    asyncio.run(rs.run_once(rid))

    second = asyncio.run(rs.run_once(rid))

    assert second["ok"] is False
    assert second["error"] == "not_runnable"


def test_a_working_preset_is_untouched(store) -> None:
    """R-0005 control — a real preset must not be paused by this path.

    Asserts on the governor verdict rather than on run success: whether the DAG
    produces output depends on optional extras, but a routine on an implemented
    runner must never be parked as permanently broken.
    """
    rid = _make("minimal")

    out = asyncio.run(rs.run_once(rid))

    assert out["governor"] != "paused:permanent"
    assert "permanent" not in (rs.get_routine(rid)["paused_reason"] or "")


def test_ordinary_failures_still_use_the_streak(store, monkeypatch) -> None:
    """A flaky routine must not be parked on its first bad run.

    That distinction is the reason the streak exists, so removing it for
    everything would trade one wrong behaviour for another.
    """
    rid = _make("minimal")

    async def _flaky(*a, **k):
        return {"ok": False, "error": "transient"}

    monkeypatch.setattr(rs, "_dispatch", _flaky)

    out = asyncio.run(rs.run_once(rid))

    assert out["governor"] != "paused:permanent"
    assert rs.get_routine(rid)["status"] != "paused"
