"""Scoreboard + JEPA family gate tests (data/engine/scoreboard.db isolated per test)."""

from __future__ import annotations

import pytest

from CortexOS.execution import scoreboard


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    scoreboard.init()


def test_embed_is_deterministic_and_normalized():
    a = scoreboard.embed_goal("fetch sales data from warehouse")
    b = scoreboard.embed_goal("fetch sales data from warehouse")

    assert a == b
    assert len(a) == scoreboard.EMBED_DIM
    assert sum(x * x for x in a) == pytest.approx(1.0, abs=1e-9)


def test_embed_similarity_orders_topics():
    from CortexOS.memory.store import cosine

    sales = scoreboard.embed_goal("fetch sales data from the warehouse database")
    inventory = scoreboard.embed_goal("fetch inventory data from the warehouse database")
    poem = scoreboard.embed_goal("write a creative marketing poem about launch day")

    assert cosine(sales, inventory) > cosine(sales, poem)


def test_family_id_deterministic():
    assert scoreboard.family_id("Fetch sales data!") == scoreboard.family_id("fetch sales data")


def test_record_and_family_stats():
    scoreboard.record_run("r1", "fam-a", "dag", mode="probe", score=1.0, cost_myr=0.1, latency_ms=20)
    scoreboard.record_run("r2", "fam-a", "dag", mode="scaled", score=1.0, cost_myr=0.2, latency_ms=30)
    scoreboard.record_run("r3", "fam-a", "minimal", mode="probe", score=0.0)

    stats = scoreboard.family_stats("fam-a")

    assert stats["dag"]["runs"] == 2
    assert stats["dag"]["scaled_runs"] == 1
    assert stats["dag"]["mean_score"] == pytest.approx(1.0)
    assert stats["minimal"]["mean_score"] == pytest.approx(0.0)


def test_best_preset_respects_min_runs_and_positive_score():
    for i in range(2):
        scoreboard.record_run(f"r{i}", "fam-b", "dag", score=1.0)
    assert scoreboard.best_preset("fam-b", min_runs=3) is None

    scoreboard.record_run("r2", "fam-b", "dag", score=1.0)
    winner = scoreboard.best_preset("fam-b", min_runs=3)
    assert winner is not None and winner["preset"] == "dag"

    for i in range(3):
        scoreboard.record_run(f"z{i}", "fam-zero", "minimal", score=0.0)
    assert scoreboard.best_preset("fam-zero", min_runs=3) is None


def test_best_preset_prefers_score_then_cost():
    for i in range(3):
        scoreboard.record_run(f"a{i}", "fam-c", "dag", score=1.0, cost_myr=0.5)
        scoreboard.record_run(f"b{i}", "fam-c", "minimal", score=1.0, cost_myr=0.1)
        scoreboard.record_run(f"c{i}", "fam-c", "rag", score=0.5, cost_myr=0.01)

    winner = scoreboard.best_preset("fam-c", min_runs=3)

    assert winner["preset"] == "minimal"  # ties on score break toward cheap


def test_centroid_running_mean_and_match():
    vec1 = scoreboard.embed_goal("fetch sales data from warehouse")
    vec2 = scoreboard.embed_goal("fetch revenue data from warehouse")
    scoreboard.upsert_family("fam-d", vec1)
    scoreboard.upsert_family("fam-d", vec2)

    families = scoreboard.list_families()
    assert families[0]["family"] == "fam-d"
    assert families[0]["run_count"] == 2

    family, sim = scoreboard.match_family(vec1)
    assert family == "fam-d"
    assert sim > 0.5


def test_should_race_cold_start_then_confident():
    goal = "fetch sales data from the warehouse database"
    cold = scoreboard.should_race(goal)
    assert cold["race"] is True
    assert cold["reason"] == "no_family_match"

    fam = scoreboard.family_id(goal)
    scoreboard.upsert_family(fam, scoreboard.embed_goal(goal))
    for i in range(3):
        scoreboard.record_run(f"s{i}", fam, "dag", mode="scaled", score=1.0)

    similar = scoreboard.should_race("fetch sales data from the warehouse database now")
    assert similar["race"] is False
    assert similar["winner"] == "dag"
    assert similar["reason"] == "family_confident"

    unrelated = scoreboard.should_race("compose a birthday song for grandma")
    assert unrelated["race"] is True


def test_should_race_known_family_without_history():
    goal = "summarize the quarterly compliance report"
    fam = scoreboard.family_id(goal)
    scoreboard.upsert_family(fam, scoreboard.embed_goal(goal))

    gate = scoreboard.should_race(goal)

    assert gate["race"] is True
    assert gate["reason"] == "insufficient_history"
    assert gate["family"] == fam
