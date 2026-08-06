"""A preset that executed nothing must not outscore one that tried and failed.

`computer_control` resolves to `_ontology_actions`, which needs an `action_id`
to do anything. Handed a real goal and no action id it returned:

    {"ok": True, "runner": "ontology_actions", "status": "ready"}

Measured, with the goal "open the app and log in". Nothing ran. `ok` was True.

That is worse than a plain false green, because of where the value lands. The
racing router scores an `ok` probe with no output at **0.5**, and a preset that
genuinely attempted the work and failed at **0.0**. So in any race over a
computer-use goal, the architecture that does nothing beats the one that tried —
and the scoreboard learns that doing nothing is the best known approach for
that family.

That corrupts the loop precisely where it was just repaired: `known` became
reachable, so what gets written now becomes what future goals inherit. A no-op
winner is a stored winner.

The rule these pin: **executing nothing is not a partial success.** Ranking
evidence has to come from work actually attempted.
"""

from __future__ import annotations

import asyncio

from CortexOS.execution.preset_router import plan_for_request
from CortexOS.execution.race_router import score_probe
from CortexOS.execution.run_plan import execute_run_plan


def _run(body: dict) -> dict:
    return asyncio.run(execute_run_plan(plan_for_request("computer_control", {}), body))


def test_computer_control_without_an_action_is_not_ok() -> None:
    """Being handed no action to run is a planning failure, not a success."""
    result = _run({"prompt": "open the billing app and log in"})

    assert result["ok"] is False


def test_it_says_why_rather_than_reporting_ready() -> None:
    """`status: ready` told a caller the opposite of what happened."""
    result = _run({"prompt": "open the billing app and log in"})

    blob = f"{result.get('status', '')} {result.get('error', '')}".lower()
    assert "no action" in blob or "action_id" in blob


def test_a_no_op_probe_scores_zero() -> None:
    """The scoring half — this is what reached the scoreboard."""
    assert score_probe({"ok": True, "status": "ready"})["score"] == 0.0


def test_a_no_op_never_outranks_an_honest_failure() -> None:
    """The comparison that decided races, stated directly."""
    no_op = score_probe({"ok": True, "status": "ready"})["score"]
    tried_and_failed = score_probe({"ok": False, "error": "click target not found"})["score"]

    assert no_op <= tried_and_failed


def test_real_work_still_scores(monkeypatch) -> None:
    """R-0005 control: a probe that produced something must still win.

    Tightening the no-output case must not flatten every score to zero, or the
    racing router stops being able to pick a winner at all.
    """
    assert score_probe({"ok": True, "output": "invoice drafted"})["score"] > 0.0


def test_an_executed_action_with_empty_output_still_counts(monkeypatch) -> None:
    """Not everything useful returns text.

    A click that worked has no output but did happen. It is distinguished from
    the no-op by having actually run, so it keeps partial credit rather than
    being punished for being quiet.
    """
    scored = score_probe({"ok": True, "status": "completed", "result": {"ok": True}})

    assert scored["score"] > 0.0
