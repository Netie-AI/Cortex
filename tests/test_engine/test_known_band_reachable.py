"""OSR must be able to reach `known` — the band Pointer needs to land.

OSR bands work as known | near | open, and only `known` routes to a family's
stored winner. `known` requires two things at once: similarity above tau, *and*
`scoreboard.best_preset(family)` returning a proven winner. Similarity alone
never earns it, which is correct and deliberate.

But on this machine the scoreboard was empty — `arch_families: 0`,
`arch_runs: 0` — so `best_preset` returned None for every family and `known`
was **structurally unreachable**. Measured, not inferred: classifying the same
text twice returned `open` both times.

That is what stops Pointer landing. Pointer consumes OSR bands; with no
families it is told every action is novel forever and can never inherit a plan
that was already validated for that shape of work.

These tests pin the loop itself: a family that has won gets `known` and its
winner back, and an unproven one does not. They are about reachability, not
about the embedding — swapping the feature hash for a learned representation
later must keep this property, not establish it.
"""

from __future__ import annotations

import pytest

from CortexOS.execution import osr, scoreboard

GOAL = "reconcile the weekly warehouse shipment report"


@pytest.fixture
def clean_board(tmp_path, monkeypatch):
    """Isolated scoreboard — never the engine's live one."""
    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    scoreboard.init()
    return tmp_path


def _win(goal: str, preset: str = "minimal", *, runs: int = 3) -> str:
    """Record `runs` scaled wins for a goal's family, as a real race would."""
    family = scoreboard.family_id(goal)
    scoreboard.upsert_family(family, scoreboard.embed_goal(goal))
    for i in range(runs):
        scoreboard.record_run(
            f"seed-{preset}-{i}",
            family,
            preset,
            mode="scaled",
            score=1.0,
            predicates_pass=True,
            judge_score=None,
            latency_ms=10,
        )
    return family


def test_empty_scoreboard_cannot_produce_known(clean_board) -> None:
    """The starting condition, pinned so the fix cannot be misread.

    This is correct behaviour, not the bug. The bug was that nothing ever left
    this state.
    """
    assert scoreboard.best_preset(scoreboard.family_id(GOAL)) is None
    assert osr.classify(GOAL)["band"] != osr.BAND_KNOWN


def test_a_proven_family_reaches_known(clean_board) -> None:
    """The property Pointer depends on: proven work stops being treated as novel."""
    _win(GOAL)

    result = osr.classify(GOAL)

    assert result["band"] == osr.BAND_KNOWN
    assert result["winner"] == "minimal"
    assert result["family_id"]


def test_known_returns_the_winner_to_route_to(clean_board) -> None:
    """A band with no winner attached is not actionable by a caller."""
    _win(GOAL, preset="dag")

    assert osr.classify(GOAL)["winner"] == "dag"


def test_similarity_alone_still_never_earns_known(clean_board) -> None:
    """The G2.3 honesty rule must survive this: a family with no proven winner
    bands `near`, however familiar the words look."""
    family = scoreboard.family_id(GOAL)
    scoreboard.upsert_family(family, scoreboard.embed_goal(GOAL))
    for i in range(3):
        scoreboard.record_run(
            f"loss-{i}", family, "minimal", mode="scaled",
            score=0.0, predicates_pass=False, judge_score=None, latency_ms=10,
        )

    assert scoreboard.best_preset(family) is None
    assert osr.classify(GOAL)["band"] != osr.BAND_KNOWN


def test_an_unrelated_goal_does_not_inherit_the_winner(clean_board) -> None:
    """Bands are per family. Proving one must not silently promote everything."""
    _win(GOAL)

    other = osr.classify("book a flight to Tokyo next Tuesday")

    assert other["band"] != osr.BAND_KNOWN
    assert other.get("winner") is None
