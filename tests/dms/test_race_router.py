"""Racing router tests — top-3 probe race, predicate-first scoring, scale winner."""

from __future__ import annotations

import pytest

from CortexOS.execution import race_router, scoreboard
from CortexOS.execution.race_router import COLD_START_ORDER


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    scoreboard.init()


def test_rank_candidates_cold_start():
    assert race_router.rank_candidates("fam-new") == list(COLD_START_ORDER)


def test_rank_candidates_uses_history():
    for i in range(3):
        scoreboard.record_run(f"a{i}", "fam-h", "dag", score=1.0, cost_myr=0.5)
        scoreboard.record_run(f"b{i}", "fam-h", "minimal", score=0.2, cost_myr=0.1)

    ranked = race_router.rank_candidates("fam-h")

    assert ranked[0] == "dag"
    assert len(ranked) == 3
    assert len(set(ranked)) == 3


def test_predicates_fail_closed_on_unknown_type():
    assert race_router.eval_predicates("anything", [{"type": "mystery"}]) is False


def test_score_probe_predicate_outranks_judge():
    result = {"ok": True, "output": "hello world"}

    scored = race_router.score_probe(
        result, [{"type": "contains", "value": "zzz"}], judge=lambda r: 1.0
    )

    assert scored["score"] == 0.0
    assert scored["predicates_pass"] is False
    assert scored["judge_score"] == 1.0  # recorded, but powerless


def test_score_probe_judge_fills_when_no_predicates():
    scored = race_router.score_probe({"ok": True, "output": "x"}, None, judge=lambda r: 0.4)

    assert scored["score"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_race_probes_all_candidates_and_scales_winner():
    out = await race_router.race(
        "say hello",
        {"prompt": "hello"},
        predicates=[{"type": "contains", "value": "hello"}],
    )

    assert out["mode"] == "raced"
    assert out["candidates"] == list(COLD_START_ORDER)
    assert len(out["probes"]) == 3
    assert all(p["score"] == 1.0 for p in out["probes"])
    assert out["winner"] == "minimal"  # tie on score breaks toward cheap-first order
    assert out["all_probes_failed"] is False
    assert out["scaled"] is not None and out["scaled"]["score"] == 1.0

    stats = scoreboard.family_stats(out["family"])
    total_rows = sum(s["runs"] for s in stats.values())
    assert total_rows == 4  # 3 probes + 1 scaled
    assert stats["minimal"]["scaled_runs"] == 1


@pytest.mark.asyncio
async def test_race_all_probes_failed_skips_scale():
    out = await race_router.race(
        "impossible",
        {"prompt": "impossible"},
        candidates=["langgraph", "langchain"],  # honest adapter_unavailable
    )

    assert out["all_probes_failed"] is True
    assert out["winner"] == "langgraph"
    assert out["scaled"] is None


@pytest.mark.asyncio
async def test_auto_route_races_cold_then_routes_direct():
    goal = "fetch sales data from the warehouse database"
    predicates = [{"type": "nonempty"}]

    first = await race_router.auto_route(goal, predicates=predicates, min_runs=1)
    assert first["mode"] == "raced"
    assert first["reason"] == "no_family_match"
    assert first["scaled"] is not None

    second = await race_router.auto_route(goal, predicates=predicates, min_runs=1)
    assert second["mode"] == "direct"
    assert second["reason"] == "family_confident"
    assert second["winner"] == first["winner"]
    assert second["result"]["ok"] is True


@pytest.mark.asyncio
async def test_auto_route_direct_still_records_and_teaches():
    goal = "fetch sales data from the warehouse database"
    await race_router.auto_route(goal, predicates=[{"type": "nonempty"}], min_runs=1)
    before = scoreboard.list_families()[0]["run_count"]

    await race_router.auto_route(goal, predicates=[{"type": "nonempty"}], min_runs=1)

    after = scoreboard.list_families()[0]["run_count"]
    assert after == before + 1
