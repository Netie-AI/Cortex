"""gen-cFSM P1 tests — execute + collapse audit, false-pass catch, regenerate loop."""

from __future__ import annotations

import pytest

from CortexOS.execution import race_router, scoreboard
from CortexOS.execution.gen_cfsm import (
    VERDICT_FAIL,
    VERDICT_FALSE_PASS,
    VERDICT_PASS,
    execute_cfsm,
    generate_ir,
    iterate_cfsm,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    scoreboard.init()


@pytest.mark.asyncio
async def test_execute_passes_with_predicates():
    report = await execute_cfsm(
        generate_ir("say hello", 3),
        {"prompt": "hello"},
        predicates=[{"type": "contains", "value": "hello"}],
    )

    assert report["ok"] is True
    assert report["verdict"] == VERDICT_PASS
    assert report["stage"] == "audit"
    assert report["node_count"] == 3
    assert 0.0 <= report["collapse"] <= 1.0


@pytest.mark.asyncio
async def test_execute_catches_false_pass():
    """Collapse says 'done', predicates say no — the audit's whole job."""
    report = await execute_cfsm(
        generate_ir("hello", 3),
        {"prompt": "hello"},
        predicates=[{"type": "contains", "value": "zzz"}],
        tau=0.3,  # output echoes the goal, so collapse clears a modest tau
    )

    assert report["ok"] is False
    assert report["collapse"] >= 0.3
    assert report["verdict"] == VERDICT_FALSE_PASS


@pytest.mark.asyncio
async def test_execute_plain_fail_when_far_from_goal():
    report = await execute_cfsm(
        generate_ir("hello", 3),
        {"prompt": "hello"},
        predicates=[{"type": "contains", "value": "zzz"}],
        tau=0.99,
    )

    assert report["ok"] is False
    assert report["verdict"] == VERDICT_FAIL


@pytest.mark.asyncio
async def test_execute_rejects_bad_ir_at_compile_stage():
    ir = generate_ir("goal", 3)
    ir["horizon"] = 4

    report = await execute_cfsm(ir, {"prompt": "x"})

    assert report["ok"] is False
    assert report["stage"] == "compile"


@pytest.mark.asyncio
async def test_iterate_succeeds_first_horizon_and_records():
    out = await iterate_cfsm(
        "say hello", {"prompt": "hello"}, predicates=[{"type": "nonempty"}]
    )

    assert out["ok"] is True
    assert out["regenerations"] == 0
    assert out["final"]["horizon"] == 3

    stats = scoreboard.family_stats(out["family"])
    assert stats["gen_cfsm"]["runs"] == 1
    assert stats["gen_cfsm"]["mean_score"] == pytest.approx(1.0)
    assert scoreboard.list_families()[0]["family"] == out["family"]


@pytest.mark.asyncio
async def test_iterate_escalates_horizons_then_reports_failure():
    out = await iterate_cfsm(
        "impossible goal", {"prompt": "hello"}, predicates=[{"type": "contains", "value": "zzz"}]
    )

    assert out["ok"] is False
    assert out["regenerations"] == 2
    assert [a["horizon"] for a in out["attempts"]] == [3, 5, 7]

    stats = scoreboard.family_stats(out["family"])
    assert stats["gen_cfsm"]["runs"] == 3
    assert stats["gen_cfsm"]["mean_score"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_gen_cfsm_races_as_candidate():
    out = await race_router.race(
        "say hello",
        {"prompt": "hello"},
        candidates=["gen_cfsm", "minimal"],
        predicates=[{"type": "nonempty"}],
    )

    assert [p["preset"] for p in out["probes"]] == ["gen_cfsm", "minimal"]
    assert all(p["score"] == 1.0 for p in out["probes"])
    assert out["winner"] == "gen_cfsm"  # tie breaks toward earlier candidate
    assert out["scaled"] is not None

    stats = scoreboard.family_stats(out["family"])
    assert stats["gen_cfsm"]["scaled_runs"] == 1
