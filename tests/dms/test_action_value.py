"""G2.2 — value table, outcome learning, ranking, and F1 ledger wiring.

The two properties that must hold no matter what: ranking while cold is
identical to the cosine ranking that came before (so the silence litmus cannot
regress), and the audit trail records decisions without copying prose into it.
"""

from __future__ import annotations

import pytest

from CortexOS.execution import action_value, enterprise_goal, goal_audit, seeker


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(action_value, "DB_PATH", tmp_path / "action_value.db")
    monkeypatch.setattr(enterprise_goal, "DB_PATH", tmp_path / "goals.db")
    monkeypatch.setattr(goal_audit, "LEDGER_DB_PATH", tmp_path / "ledger.db")
    from CortexOS.execution import routine_scheduler, scoreboard

    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    monkeypatch.setattr(routine_scheduler, "DB_PATH", tmp_path / "routines.db")
    # G2.4/G2.5 gave the seeker two more stores — isolate them in the same commit.
    from CortexOS.execution import action_event, commitments

    monkeypatch.setattr(action_event, "DB_PATH", tmp_path / "action_events.db")
    monkeypatch.setattr(commitments, "DB_PATH", tmp_path / "commitments.db")
    action_value.init()
    enterprise_goal.init()


def _goal(**kwargs):
    return enterprise_goal.create_goal(
        kwargs.pop("statement", "Grow monthly recurring revenue ethically"),
        measurable_criteria=kwargs.pop(
            "measurable_criteria",
            [{"name": "revenue", "metric": "mrr", "target": 100000, "evidence_source": "billing"}],
        ),
        **kwargs,
    )["goal"]


def _ledger_rows(tmp_path):
    from packs.dms.audit import ledger

    return ledger.list_entries(db_path=tmp_path / "ledger.db", limit=500)


# --- the value estimate ------------------------------------------------------


def test_cold_value_is_exactly_the_prior():
    """No evidence must mean no change — this is what keeps seek working cold."""
    estimate = action_value.value("fam", "check_metric", "criterion", prior=0.42)

    assert estimate["value"] == pytest.approx(0.42)
    assert estimate["learned"] is False
    assert estimate["n"] == 0


def test_evidence_pulls_the_estimate_off_the_prior():
    for i in range(3):
        action_value.record_outcome("fam", "check_metric", "criterion", "accepted")

    estimate = action_value.value("fam", "check_metric", "criterion", prior=0.0)

    assert estimate["learned"] is True
    assert estimate["n"] == 3
    assert estimate["value"] > 0.0  # was 0.0 cold, evidence lifted it
    assert estimate["mean_reward"] == pytest.approx(1.0)


def test_one_outcome_cannot_outrank_a_strong_prior_outright():
    """Shrinkage: a single accept must not beat a much more relevant action."""
    action_value.record_outcome("fam", "summarize", "criterion", "accepted")

    lucky = action_value.value("fam", "summarize", "criterion", prior=0.0)
    relevant = action_value.value("fam", "check_metric", "criterion", prior=0.9)

    assert relevant["value"] > lucky["value"]


def test_failures_push_the_estimate_down():
    for _ in range(4):
        action_value.record_outcome("fam", "draft_routine", "pattern", "failed")

    assert action_value.value("fam", "draft_routine", "pattern", prior=0.8)["value"] < 0.8


def test_unknown_outcome_is_refused_not_guessed():
    out = action_value.record_outcome("fam", "inspect", "open_loop", "vibes")

    assert out["ok"] is False
    assert out["error"] == "unknown_outcome:vibes"


def test_rewards_are_clamped():
    action_value.record_outcome("fam", "inspect", "open_loop", "accepted", reward=99.0)

    assert action_value.value("fam", "inspect", "open_loop", prior=1.0)["mean_reward"] == 1.0


def test_explain_never_shows_a_bare_number():
    cold = action_value.explain(action_value.value("fam", "inspect", "open_loop", prior=0.5))
    assert "No history yet" in cold

    for _ in range(3):
        action_value.record_outcome("fam", "inspect", "open_loop", "accepted")
    warm = action_value.explain(action_value.value("fam", "inspect", "open_loop", prior=0.5))
    assert "3 past outcomes" in warm and "worth doing" in warm


# --- ranking -----------------------------------------------------------------


def test_cold_seek_ranking_is_unchanged_by_the_value_table():
    """Silence litmus protection: cold V == cosine, so order must match relevance."""
    goal = _goal()

    out = seeker.seek(goal["id"])

    assert out["ok"] is True and out["proposals"]
    for proposal in out["proposals"]:
        assert proposal["value"] == pytest.approx(proposal["relevance"])
        assert proposal["value_learned"] is False
    ordered = [p["relevance"] for p in out["proposals"]]
    assert ordered == sorted(ordered, reverse=True)


def test_learned_action_rises_above_a_more_relevant_but_unproven_one():
    goal = _goal()
    family = action_value.goal_family(goal)
    first = seeker.seek(goal["id"])["proposals"]
    top_before = first[0]
    lower = next(p for p in first if p["action"] != top_before["action"])

    for _ in range(6):
        action_value.record_outcome(family, lower["action"], lower["source"], "accepted")
    for _ in range(6):
        action_value.record_outcome(family, top_before["action"], top_before["source"], "failed")

    after = seeker.seek(goal["id"])["proposals"]

    assert after[0]["action"] == lower["action"]
    assert after[0]["value_learned"] is True
    assert after[0]["value"] > after[0]["relevance"]


def test_learning_never_unlocks_autonomy():
    """A well-liked action is still draft-only — value is not permission."""
    goal = _goal()
    family = action_value.goal_family(goal)
    for _ in range(10):
        action_value.record_outcome(family, "check_metric", "criterion", "accepted")

    out = seeker.seek(goal["id"])

    assert out["autonomy_level"] == "draft_only"
    assert all(p["auto_ok"] is False for p in out["proposals"])


def test_outcome_loop_looks_up_action_from_the_stored_seek():
    goal = _goal()
    proposal = seeker.seek(goal["id"])["proposals"][0]

    out = seeker.record_proposal_outcome(goal["id"], proposal["id"], "accepted")

    assert out["ok"] is True
    assert action_value.value(
        action_value.goal_family(goal), proposal["action"], proposal["source"], prior=0.0
    )["n"] == 1


def test_outcome_for_an_unknown_proposal_is_refused():
    goal = _goal()
    assert seeker.record_proposal_outcome(goal["id"], "prop-nope", "accepted")["error"] == (
        "unknown_proposal"
    )


# --- F1 ledger ---------------------------------------------------------------


def test_binding_a_goal_is_ledgered(tmp_path):
    out = enterprise_goal.create_goal("Grow revenue ethically")

    assert out["audit"]["ok"] is True
    rows = _ledger_rows(tmp_path)
    assert any(r.event_type == goal_audit.EVENT_GOAL_BOUND for r in rows)


def test_seek_is_ledgered_as_proactive(tmp_path):
    goal = _goal()

    out = seeker.seek(goal["id"], trigger="idle")

    assert out["audit"]["ok"] is True
    entry = next(r for r in _ledger_rows(tmp_path) if r.event_type == goal_audit.EVENT_SEEK)
    assert entry.payload["initiative"] == "proactive"
    assert entry.payload["trigger"] == "idle"
    assert entry.payload["proposal_count"] == len(out["proposals"])
    assert entry.payload["auto_ok"] == 0  # proves nothing self-authorised


def test_false_pass_and_constraint_violation_are_ledgered(tmp_path):
    goal = _goal()

    false_pass = enterprise_goal.evaluate_termination(
        goal, collapse=0.99, predicate_results=[{"type": "nonempty", "pass": False}]
    )
    assert false_pass["verdict"] == "false_pass_caught"
    assert false_pass["audit"]["ok"] is True

    violated = enterprise_goal.evaluate_termination(
        goal,
        collapse=0.99,
        predicate_results=[{"type": "nonempty", "pass": True}],
        violates=["no_deception"],
    )
    assert violated["verdict"] == "constraint_violated"

    kinds = [
        r.payload.get("verdict")
        for r in _ledger_rows(tmp_path)
        if r.event_type == goal_audit.EVENT_TERMINATION_BLOCKED
    ]
    assert "false_pass_caught" in kinds and "constraint_violated" in kinds


def test_a_successful_run_is_not_an_audit_event(tmp_path):
    """Only refusals are logged — 'it worked' is ordinary operation."""
    goal = _goal()

    enterprise_goal.evaluate_termination(
        goal, collapse=0.9, predicate_results=[{"type": "nonempty", "pass": True}]
    )

    assert not [
        r for r in _ledger_rows(tmp_path) if r.event_type == goal_audit.EVENT_TERMINATION_BLOCKED
    ]


def test_ledger_records_decisions_not_prose(tmp_path):
    """The audit trail must not become a second copy of the user's data."""
    secret_ish = "Grow revenue for Acme Holdings via the Q3 pricing memo"
    goal = _goal(statement=secret_ish)
    seeker.seek(goal["id"])

    blob = "".join(str(r.payload) for r in _ledger_rows(tmp_path))

    assert "Acme Holdings" not in blob
    assert "pricing memo" not in blob
    assert goal["id"] in blob  # the identifier is there, the content is not


def test_audit_failure_is_reported_not_swallowed(monkeypatch, tmp_path):
    monkeypatch.setattr(goal_audit, "LEDGER_DB_PATH", tmp_path / "nope" / "x" / "ledger.db")

    def _boom(*args, **kwargs):
        raise OSError("ledger unavailable")

    from packs.dms.audit import ledger

    monkeypatch.setattr(ledger, "append", _boom)
    goal = _goal()

    out = seeker.seek(goal["id"])

    assert out["ok"] is True  # the engine keeps working…
    assert out["audit"]["ok"] is False  # …but says the record did not land
    assert "ledger unavailable" in out["audit"]["error"]
