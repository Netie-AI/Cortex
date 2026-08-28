"""G2.4 — ActionEvent telemetry, lossless compaction, and the law-6 weighting.

The property this slice exists to protect: the engine may learn from its own
runs, but what the *user* said must always outweigh what the engine noticed.
"""

from __future__ import annotations

import time

import pytest

from CortexOS.execution import action_event, action_value


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(action_event, "DB_PATH", tmp_path / "action_events.db")
    monkeypatch.setattr(action_value, "DB_PATH", tmp_path / "action_value.db")
    action_event.init()
    action_value.init()


def _run(outcome="succeeded", **kwargs):
    payload = {
        "initiative": action_event.INITIATIVE_SCHEDULED,
        "outcome": outcome,
        "action_kind": "check_metric",
        "source": "routine",
        "goal_family": "fam-a",
    }
    payload.update(kwargs)
    return action_event.record(**payload)


# --- schema + privacy --------------------------------------------------------


def test_a_run_is_recorded_with_the_fields_ranking_needs():
    out = _run(band="known", path="stored_winner", collapse=0.91, cost_myr=0.02, latency_ms=120)

    assert out["ok"] is True
    event = action_event.list_events()[0]
    assert event["initiative"] == "scheduled"
    assert event["outcome"] == "succeeded"
    assert event["goal_family"] == "fam-a"
    assert event["action_kind"] == "check_metric"
    assert event["band"] == "known"
    assert event["collapse"] == pytest.approx(0.91)


def test_unknown_initiative_or_outcome_is_refused_not_guessed():
    assert _run(initiative="vibes")["error"] == "unknown_initiative:vibes"
    assert _run(outcome="maybe")["error"] == "unknown_outcome:maybe"


def test_traces_carry_no_prose():
    """Same policy as goal_audit — identifiers and numbers, never content."""
    _run(
        goal_id="goal-123",
        run_id="run-abc",
        band="open",
        path="gen_cfsm",
    )

    blob = str(action_event.list_events()[0])

    assert "goal-123" in blob  # identifiers are kept
    columns = set(action_event.list_events()[0].keys())
    for forbidden in ("prompt", "output", "text", "title", "why", "payload", "statement"):
        assert forbidden not in columns


# --- teaching the ranking (weakly) -------------------------------------------


def test_a_run_teaches_the_value_table_as_inferred():
    out = _run()

    assert out["taught"]["kind"] == action_value.KIND_INFERRED
    estimate = action_value.value("fam-a", "check_metric", "routine", prior=0.0)
    assert estimate["inferred_n"] == 1
    assert estimate["explicit_n"] == 0


def test_recording_can_skip_teaching():
    _run(teach=False)

    assert action_value.value("fam-a", "check_metric", "routine", prior=0.0)["n"] == 0


def test_one_user_decision_outweighs_one_run():
    action_value.record_outcome("fam-a", "explicit_win", "src", "accepted")
    action_value.record_outcome(
        "fam-a", "inferred_win", "src", "succeeded", kind=action_value.KIND_INFERRED
    )

    explicit = action_value.value("fam-a", "explicit_win", "src", prior=0.0)
    inferred = action_value.value("fam-a", "inferred_win", "src", prior=0.0)

    assert explicit["value"] > inferred["value"]


def test_a_flood_of_runs_cannot_overrule_the_user():
    """Product law 6, as an arithmetic guarantee rather than a convention."""
    # The user said no, once.
    action_value.record_outcome("fam-a", "pushy", "src", "dismissed")
    # The engine then succeeded at it two hundred times on its own.
    for _ in range(200):
        action_value.record_outcome(
            "fam-a", "pushy", "src", "succeeded", kind=action_value.KIND_INFERRED
        )

    estimate = action_value.value("fam-a", "pushy", "src", prior=0.0)

    assert estimate["inferred_capped"] is True
    # Inferred evidence is capped at the user's own weight, so the value can
    # never climb past the midpoint between "user said no" and "runs said yes".
    assert estimate["value"] <= 0.5
    assert estimate["explicit_n"] == 1
    assert estimate["inferred_n"] == 200


def test_inferred_evidence_still_works_when_the_user_has_said_nothing():
    for _ in range(8):
        action_value.record_outcome(
            "fam-a", "quiet", "src", "succeeded", kind=action_value.KIND_INFERRED
        )

    estimate = action_value.value("fam-a", "quiet", "src", prior=0.0)

    assert estimate["inferred_capped"] is False
    assert estimate["value"] > 0.0  # learning from runs is the point of G2.4


def test_explain_says_where_the_evidence_came_from():
    action_value.record_outcome("fam-a", "mixed", "src", "accepted")
    action_value.record_outcome(
        "fam-a", "mixed", "src", "succeeded", kind=action_value.KIND_INFERRED
    )

    sentence = action_value.explain(action_value.value("fam-a", "mixed", "src", prior=0.0))

    assert "1 from your decisions" in sentence
    assert "1 from runs" in sentence


# --- compaction --------------------------------------------------------------


def test_compaction_is_lossless_for_the_counts_ranking_reads():
    old = time.time() - 40 * 86400
    for _ in range(7):
        _run("succeeded", now=old)
    for _ in range(3):
        _run("failed", now=old)
    before = action_event.outcome_counts("fam-a", "check_metric")

    result = action_event.compact()

    assert result["compacted"] == 10
    assert action_event.list_events() == []  # raw rows gone
    assert action_event.outcome_counts("fam-a", "check_metric") == before  # counts identical


def test_compaction_keeps_recent_events_raw():
    _run("succeeded")  # today
    _run("succeeded", now=time.time() - 40 * 86400)  # old

    action_event.compact()

    assert len(action_event.list_events()) == 1
    assert action_event.outcome_counts("fam-a", "check_metric")["runs"] == 2


def test_compaction_is_idempotent():
    for _ in range(4):
        _run("succeeded", now=time.time() - 40 * 86400)
    action_event.compact()
    counts = action_event.outcome_counts("fam-a", "check_metric")

    action_event.compact()
    action_event.compact()

    assert action_event.outcome_counts("fam-a", "check_metric") == counts


def test_raw_events_are_capped_so_the_store_cannot_grow_forever():
    for _ in range(12):
        _run("succeeded")

    action_event.compact(max_raw=5)

    assert len(action_event.list_events(limit=100)) == 5
    assert action_event.outcome_counts("fam-a", "check_metric")["runs"] == 12


def test_daily_rollup_preserves_totals():
    old = time.time() - 40 * 86400
    for _ in range(5):
        _run("succeeded", now=old, cost_myr=0.1, latency_ms=100)
    action_event.compact()

    day = action_event.daily("fam-a")[0]

    assert day["runs"] == 5
    assert day["succeeded"] == 5
    assert day["sum_cost_myr"] == pytest.approx(0.5)
    assert day["sum_latency_ms"] == 500


def test_summary_is_numbers_only():
    _run("succeeded", cost_myr=0.25)
    _run("failed", initiative=action_event.INITIATIVE_REACTIVE)

    summary = action_event.summary()

    assert summary["total_runs"] == 2
    assert summary["total_cost_myr"] == pytest.approx(0.25)
    assert summary["by_initiative"]["scheduled"] == 1
    assert summary["by_initiative"]["reactive"] == 1


# --- wiring ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_osr_routing_emits_a_trace(tmp_path, monkeypatch):
    from CortexOS.execution import osr, scoreboard

    monkeypatch.setattr(osr, "DB_PATH", tmp_path / "osr.db")
    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    osr.init()
    scoreboard.init()

    await osr.route("arrange the flurbo manifests", {"prompt": "arrange the flurbo manifests"})

    events = action_event.list_events()
    assert events, "routing a request should leave a trace"
    assert events[0]["initiative"] == "reactive"
    assert events[0]["band"] == osr.BAND_OPEN
    assert events[0]["path"] == "gen_cfsm"


@pytest.mark.asyncio
async def test_a_routine_run_emits_a_trace(tmp_path, monkeypatch):
    from CortexOS.execution import routine_scheduler as rs

    monkeypatch.setattr(rs, "DB_PATH", tmp_path / "routines.db")
    rs.init()
    routine = rs.create_routine("Echo", "hello there", interval_seconds=3600)

    await rs.run_once(routine["id"])

    events = action_event.list_events()
    assert events
    assert events[0]["initiative"] == "scheduled"
    assert events[0]["action_kind"].startswith("routine_")
    assert events[0]["outcome"] == "succeeded"


def test_telemetry_never_breaks_the_run_it_describes(monkeypatch):
    """A broken telemetry store must not take the engine down with it."""
    def _boom(**kwargs):
        raise OSError("telemetry store unavailable")

    monkeypatch.setattr(action_event, "record", _boom)

    from CortexOS.execution import routine_scheduler as rs

    # _record_action_event swallows and continues — proven by it not raising.
    rs._record_action_event(
        {"preset": "minimal", "prompt": "x", "predicates": [], "vars": {}},
        {"run_id": "r1"},
        ok=True,
        cost=0.0,
        started=0.0,
        finished=1.0,
    )
