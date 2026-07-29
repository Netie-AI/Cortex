"""One-sentence routines — schedule parsing, drafting, and plain-English errors.

These lock the promise that a person can type what they want in their own
words and get a correct, safe routine without touching a single setting.
"""

from __future__ import annotations

import time

import pytest

from CortexOS.execution import humanize, routine_composer, routine_scheduler as rs, schedule_spec


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "DB_PATH", tmp_path / "routines.db")
    from CortexOS.execution import scoreboard

    from CortexOS.execution import action_event, action_value

    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    # G2.4: every run now emits a trace and teaches the value table.
    monkeypatch.setattr(action_event, "DB_PATH", tmp_path / "action_events.db")
    monkeypatch.setattr(action_value, "DB_PATH", tmp_path / "action_value.db")
    rs.init()
    scoreboard.init()


# --- schedule parsing --------------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "kind", "check"),
    [
        ("every weekday morning", "weekly", lambda s: s["weekdays"] == [0, 1, 2, 3, 4] and s["at_hour"] == 9),
        ("every weekday at 9am", "weekly", lambda s: s["at_hour"] == 9),
        ("each Friday at 5pm", "weekly", lambda s: s["weekdays"] == [4] and s["at_hour"] == 17),
        ("every monday and thursday", "weekly", lambda s: s["weekdays"] == [0, 3]),
        ("every weekend", "weekly", lambda s: s["weekdays"] == [5, 6]),
        ("every 15 minutes", "interval", lambda s: s["seconds"] == 900),
        ("every 2 hours", "interval", lambda s: s["seconds"] == 7200),
        ("hourly", "interval", lambda s: s["seconds"] == 3600),
        ("every day at 5pm", "daily", lambda s: s["at_hour"] == 17),
        ("daily", "daily", lambda s: s["at_hour"] == 9),
        ("nightly", "daily", lambda s: s["at_hour"] == 21),
        ("at 08:30", "daily", lambda s: (s["at_hour"], s["at_minute"]) == (8, 30)),
        ("every evening", "daily", lambda s: s["at_hour"] == 18),
    ],
)
def test_everyday_phrasing_parses(phrase, kind, check):
    parsed = schedule_spec.parse_schedule(f"do the thing {phrase}")

    assert parsed["matched"] is True
    assert parsed["spec"]["kind"] == kind
    assert check(parsed["spec"]), parsed["spec"]


def test_no_schedule_falls_back_to_a_safe_daily_default():
    parsed = schedule_spec.parse_schedule("summarize my inbox")

    assert parsed["matched"] is False
    assert parsed["spec"] == {"kind": "daily", "at_hour": 9, "at_minute": 0}


def test_intervals_are_floored_so_nothing_hammers_the_engine():
    parsed = schedule_spec.parse_schedule("every 1 second")

    assert parsed["spec"]["seconds"] >= schedule_spec.MIN_INTERVAL_SECONDS


def test_next_occurrence_is_always_in_the_future():
    now = time.time()
    for spec in (
        {"kind": "interval", "seconds": 900},
        {"kind": "daily", "at_hour": 9, "at_minute": 0},
        {"kind": "weekly", "weekdays": [0, 1, 2, 3, 4], "at_hour": 9, "at_minute": 0},
    ):
        assert schedule_spec.next_occurrence(spec, now) > now


def test_weekly_next_occurrence_lands_on_an_allowed_day():
    now = time.time()
    spec = {"kind": "weekly", "weekdays": [5, 6], "at_hour": 10, "at_minute": 0}

    when = time.localtime(schedule_spec.next_occurrence(spec, now))

    assert when.tm_wday in (5, 6)
    assert (when.tm_hour, when.tm_min) == (10, 0)


def test_malformed_spec_never_wedges_the_scheduler():
    spec = schedule_spec.normalize_spec({"kind": "weekly", "weekdays": [99], "at_hour": 47})

    assert spec["at_hour"] < 24
    assert all(0 <= d <= 6 for d in spec["weekdays"])
    assert schedule_spec.next_occurrence({"kind": "nonsense"}, time.time()) > time.time()


def test_describe_is_human_not_seconds():
    assert schedule_spec.describe({"kind": "weekly", "weekdays": [0, 1, 2, 3, 4], "at_hour": 9, "at_minute": 0}) == (
        "Every weekday at 9:00 AM"
    )
    assert schedule_spec.describe({"kind": "interval", "seconds": 900}) == "Every 15 minutes"
    assert schedule_spec.describe({"kind": "daily", "at_hour": 17, "at_minute": 30}) == (
        "Every day at 5:30 PM"
    )


# --- composing ---------------------------------------------------------------


def test_one_sentence_fills_in_every_setting():
    draft = routine_composer.compose("Summarize my open PRs every weekday morning")

    assert draft["name"] == "Summarize my open PRs"
    assert draft["schedule"]["kind"] == "weekly"
    assert draft["schedule_text"] == "Every weekday at 9:00 AM"
    assert draft["depth"] == "high"  # "summarize" earns more than the cheapest tier
    assert draft["predicates"] == [{"type": "nonempty"}]
    assert draft["timeout_seconds"] > 0
    assert draft["daily_cost_cap_myr"] > 0
    assert draft["next_run_at"] > time.time()
    assert draft["assumptions"]  # every guess is explained in words


def test_effort_scales_with_what_was_asked():
    assert routine_composer.compose("quick check the site is up")["depth"] == "basic"
    assert routine_composer.compose("summarize the sales numbers")["depth"] == "high"
    assert routine_composer.compose("deep research on competitors")["depth"] == "max"


def test_unknown_task_defers_the_architecture_choice_to_a_race():
    draft = routine_composer.compose("wrangle the flurbo manifests")

    assert draft["preset"] == routine_composer.AUTO_PRESET
    assert any("first run" in a for a in draft["assumptions"])


def test_learned_winner_is_reused_instead_of_racing_again():
    from CortexOS.execution import scoreboard

    goal = "fetch sales data from the warehouse database"
    scoreboard.upsert_family(scoreboard.family_id(goal), scoreboard.embed_goal(goal))
    for i in range(3):
        scoreboard.record_run(f"r{i}", scoreboard.family_id(goal), "dag", mode="scaled", score=1.0)

    draft = routine_composer.compose(goal)

    assert draft["preset"] == "dag"
    assert any("already works" in a for a in draft["assumptions"])


def test_explicit_success_requirement_becomes_a_predicate():
    draft = routine_composer.compose("send the daily report, it must include revenue")

    assert {"type": "contains", "value": "revenue"} in draft["predicates"]


def test_names_drop_the_scheduling_words():
    assert "friday" not in routine_composer.compose("Draft release notes every Friday at 5pm")["name"].lower()
    assert routine_composer.compose("please can you check the disk space hourly")["name"] == "Check the disk space"


@pytest.mark.parametrize(
    ("goal", "name"),
    [
        # "on"/"at" are only schedule words when a time follows them.
        ("deep research on competitors every Monday", "Deep research on competitors"),
        ("look at the logs every hour", "Look at the logs"),
        ("check the site is up every 15 minutes", "Check the site is up"),
        ("summarize my inbox at 8am", "Summarize my inbox"),
        ("Draft release notes every Friday at 5pm", "Draft release notes"),
    ],
)
def test_names_keep_ordinary_english(goal, name):
    assert routine_composer.compose(goal)["name"] == name


def test_composing_junk_never_raises():
    for junk in ("", "   ", "???", "every every every"):
        draft = routine_composer.compose(junk)
        assert draft["name"]
        assert draft["schedule"]


# --- end to end --------------------------------------------------------------


def test_create_from_goal_produces_a_working_routine():
    routine = rs.create_from_goal("Summarize my open PRs every weekday morning")

    assert routine["name"] == "Summarize my open PRs"
    assert routine["schedule"]["kind"] == "weekly"
    assert routine["schedule_text"] == "Every weekday at 9:00 AM"
    assert routine["predicates"] == [{"type": "nonempty"}]
    assert routine["state"]["label"] == "Scheduled"
    assert routine["next_run_at"] > time.time()  # not due the instant it's made
    assert routine["assumptions"]


@pytest.mark.asyncio
async def test_scheduled_routine_advances_to_its_next_slot_not_now_plus_interval():
    routine = rs.create_from_goal("Say hello every weekday at 9am")
    rs.update_routine(routine["id"], next_run_at=time.time() - 1)  # make it due

    ran = await rs.tick()

    assert len(ran) == 1
    when = time.localtime(rs.get_routine(routine["id"])["next_run_at"])
    assert when.tm_wday in (0, 1, 2, 3, 4)
    assert (when.tm_hour, when.tm_min) == (9, 0)


@pytest.mark.asyncio
async def test_a_run_that_produces_nothing_counts_as_a_failure():
    """'It ran' is not 'it worked' — the governor must see empty output as a fail."""
    routine = rs.create_routine(
        "Empty", "x", interval_seconds=0, predicates=[{"type": "contains", "value": "impossible-token"}]
    )

    out = await rs.run_once(routine["id"])

    assert out["ok"] is False
    assert out["error"] == "goal_not_met"
    assert out["explain"]["title"]
    assert rs.get_routine(routine["id"])["error_streak"] == 1


@pytest.mark.asyncio
async def test_auto_preset_races_then_reports_what_it_chose():
    routine = rs.create_from_goal("Say hello every 15 minutes")
    assert routine["preset"] == "auto"

    out = await rs.run_once(routine["id"])

    assert out["ok"] is True
    assert out["chosen"]  # the engine tells the user which approach won


# --- plain English -----------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "port_conflict:8801",
        "process_exited:boom",
        "secrets_found",
        "stack_unknown",
        "governor:error_streak:3",
        "governor:cost_cap",
        "timeout_after:300s",
        "unsupported_stack:docker",
        "goal_not_met",
    ],
)
def test_every_known_code_gets_a_human_answer(code):
    out = humanize.explain(code)

    assert out["title"] and out["what"]
    assert not any(token in out["what"] for token in ("_", "None"))


def test_unknown_codes_stay_honest_instead_of_inventing():
    out = humanize.explain("quantum_flux_desync:42")

    assert out["title"] == "Something went wrong"
    assert out["detail"] == "quantum_flux_desync:42"  # raw code preserved, not hidden


def test_governor_pause_explains_which_limit_was_hit():
    assert "failed several times" in humanize.explain("governor:error_streak:3")["what"]
    assert "money" in humanize.explain("governor:cost_cap")["what"]


def test_routine_state_reads_like_a_person_wrote_it():
    assert humanize.routine_state({"status": "running", "enabled": True})["label"] == "Working now"
    assert humanize.routine_state({"status": "idle", "enabled": True})["label"] == "Scheduled"
    assert humanize.routine_state({"status": "idle", "enabled": False})["label"] == "Off"

    auto_paused = humanize.routine_state(
        {"status": "paused", "enabled": True, "paused_reason": "governor:cost_cap"}
    )
    assert auto_paused["label"] == "Paused automatically"
    assert auto_paused["tone"] == "warn"
