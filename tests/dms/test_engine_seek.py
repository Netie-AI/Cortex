"""G2.1 — the proactive seeker. The headline test is silence.

If the user says nothing, sends nothing and schedules nothing, the engine must
still come back with safe, goal-relevant work. And it must never quietly do the
things that need a person.
"""

from __future__ import annotations

import time

import pytest

from CortexOS.execution import enterprise_goal as eg
from CortexOS.execution import routine_scheduler as rs
from CortexOS.execution import action_value, app_store, goal_audit, scoreboard, seeker


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(eg, "DB_PATH", tmp_path / "goals.db")
    monkeypatch.setattr(rs, "DB_PATH", tmp_path / "routines.db")
    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    # G2.2 gave the seeker two more stores. Without these the value table and
    # ledger fall back to the repo's live databases, and real usage leaks in.
    monkeypatch.setattr(action_value, "DB_PATH", tmp_path / "action_value.db")
    monkeypatch.setattr(goal_audit, "LEDGER_DB_PATH", tmp_path / "ledger.db")
    # G2.5: the seeker now also arms open commitments.
    from CortexOS.execution import action_event, commitments

    monkeypatch.setattr(commitments, "DB_PATH", tmp_path / "commitments.db")
    monkeypatch.setattr(action_event, "DB_PATH", tmp_path / "action_events.db")
    eg.init()
    rs.init()
    scoreboard.init()
    app_store.init()
    action_value.init()


def _goal(**kwargs):
    return eg.create_goal(
        kwargs.pop("statement", "Grow monthly revenue ethically"), **kwargs
    )["goal"]


# --- the silence litmus ------------------------------------------------------


def test_silence_litmus_empty_everything_still_produces_work():
    """No inbox, no routines, no apps, no events — the engine still acts."""
    goal = _goal()

    out = seeker.seek(goal["id"])

    assert out["ok"] is True
    assert out["initiative"] == "proactive"
    assert len(out["proposals"]) >= 1
    assert all(p["title"] and p["why"] for p in out["proposals"])
    assert out["assumptions"]


def test_a_bare_goal_is_told_it_cannot_be_measured():
    goal = _goal(measurable_criteria=[])

    titles = [p["title"] for p in seeker.seek(goal["id"])["proposals"]]

    assert any("measurable" in t.lower() for t in titles)


def test_criteria_become_standing_questions():
    goal = _goal(
        measurable_criteria=[
            {"name": "monthly revenue", "metric": "mrr", "target": 100000, "evidence_source": "lakehouse"}
        ]
    )

    titles = [p["title"] for p in seeker.seek(goal["id"])["proposals"]]

    assert any("monthly revenue" in t for t in titles)


def test_a_criterion_with_no_evidence_source_becomes_work():
    goal = _goal(measurable_criteria=[{"name": "churn", "metric": "churn"}])

    proposals = seeker.seek(goal["id"])["proposals"]

    assert any("measured from" in p["title"] for p in proposals)
    assert any("target" in p["title"].lower() for p in proposals)


# --- open loops in the engine's own state ------------------------------------


def test_a_governor_paused_routine_becomes_a_proposal():
    goal = _goal()
    routine = rs.create_routine("Nightly digest", "summarize the day")
    rs.pause(routine["id"], "governor:error_streak:3")

    titles = [p["title"] for p in seeker.seek(goal["id"])["proposals"]]

    assert any("Nightly digest" in t for t in titles)


def test_an_app_waiting_on_approval_is_surfaced_but_never_approved():
    import io
    import zipfile

    goal = _goal()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("index.html", "<h1>hi</h1>")
    record = app_store.import_zip_bytes(buffer.getvalue(), name="Waiting App")["app"]
    assert record["status"] == "draft"

    out = seeker.seek(goal["id"])
    match = next(p for p in out["proposals"] if "Waiting App" in p["title"])

    assert match["action"] != "approve_app"  # proposing a review is not approving
    assert app_store.get_app(record["id"])["status"] == "draft"  # untouched


# --- safety ------------------------------------------------------------------


def test_the_seeker_never_proposes_doing_something_irreversible_itself():
    goal = _goal(soft_preferences={"autonomy_level": "safe_auto"})

    for proposal in seeker.seek(goal["id"])["proposals"]:
        if proposal["auto_ok"]:
            assert proposal["action"] in eg.SAFE_ACTIONS
            assert proposal["risk"] == eg.RISK_SAFE


def test_draft_only_goals_do_nothing_on_their_own():
    goal = _goal()  # default autonomy

    out = seeker.seek(goal["id"])

    assert out["autonomy_level"] == "draft_only"
    assert all(p["auto_ok"] is False for p in out["proposals"])
    assert any("draft only" in a for a in out["assumptions"])


def test_assumptions_are_always_shown_in_words():
    goal = _goal()

    assumptions = seeker.seek(goal["id"])["assumptions"]

    assert any("Nobody asked" in a for a in assumptions)
    assert any("never send messages" in a.lower() for a in assumptions)
    assert all("_" not in a for a in assumptions)  # no engine vocabulary leaking


def test_no_goal_bound_is_a_sentence_not_a_crash():
    out = seeker.seek("nope")

    assert out["ok"] is False
    assert out["error"] == "no_goal_bound"


def test_proposals_are_ranked_by_closeness_to_the_goal():
    goal = _goal(
        statement="Grow monthly recurring revenue ethically",
        measurable_criteria=[
            {"name": "monthly recurring revenue", "metric": "mrr", "target": 1, "evidence_source": "db"}
        ],
    )

    proposals = seeker.seek(goal["id"])["proposals"]

    relevances = [p["relevance"] for p in proposals]
    assert relevances == sorted(relevances, reverse=True)
    assert relevances[0] > 0


def test_seeks_are_recorded_for_the_ui():
    goal = _goal()
    seeker.seek(goal["id"], trigger="idle")

    seeks = eg.list_seeks(goal["id"])

    assert seeks and seeks[0]["trigger"] == "idle"
    assert seeks[0]["proposals"]


# --- the always-on half ------------------------------------------------------


def test_idle_seek_yields_to_scheduled_work():
    goal = _goal()
    rs.create_routine("Due now", "do the thing", interval_seconds=60)  # next_run_at = now

    assert seeker.seek_if_idle(goal["id"]) is None


def test_idle_seek_runs_when_nothing_is_due():
    goal = _goal()
    routine = rs.create_routine("Later", "do the thing", interval_seconds=3600)
    rs.update_routine(routine["id"], next_run_at=time.time() + 3600)

    out = seeker.seek_if_idle(goal["id"])

    assert out is not None
    assert out["trigger"] == "idle"
    assert out["proposals"]


def test_idle_seek_respects_the_engine_budget(monkeypatch):
    goal = _goal()
    routine = rs.create_routine("Later", "x", interval_seconds=3600)
    rs.update_routine(routine["id"], next_run_at=time.time() + 3600, cost_today=99.0, cost_day=rs._today())
    monkeypatch.setattr(rs, "GLOBAL_DAILY_COST_CAP_MYR", 1.0)

    assert seeker.seek_if_idle(goal["id"]) is None


# --- routes ------------------------------------------------------------------


def test_seek_route_works_with_an_empty_body(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    from fastapi.testclient import TestClient

    from CortexOS.api.app import create_app

    client = TestClient(create_app())

    created = client.post(
        "/api/goals",
        json={
            "statement": "Grow revenue ethically",
            "measurable_criteria": [{"name": "mrr", "metric": "mrr", "evidence_source": "db"}],
        },
    ).json()
    assert created["ok"] is True
    goal_id = created["goal"]["id"]

    seek = client.post("/api/engine/seek", json={"goal_id": goal_id}).json()
    assert seek["initiative"] == "proactive"
    assert seek["proposals"]
    assert seek["assumptions"]

    # No body at all — the active engine shouldn't need to be told anything.
    bare = client.post("/api/engine/seek").json()
    assert bare["ok"] is True

    history = client.get(f"/api/goals/{goal_id}/seeks").json()
    assert history["seeks"]

    assert client.post("/api/goals", json={"statement": ""}).status_code == 400
    assert client.get("/api/goals/ghost").status_code == 404
