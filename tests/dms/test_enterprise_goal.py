"""G2.0 — the ethical goal the engine may pursue on its own.

The two properties worth defending: a goal can never exist without its ethical
floor, and confidence alone can never authorise an action.
"""

from __future__ import annotations

import pytest

from CortexOS.execution import enterprise_goal as eg
from CortexOS.execution import goal_audit


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(eg, "DB_PATH", tmp_path / "goals.db")
    # G2.2 made goal writes land on the F1 ledger. Unpatched, that is the DMS
    # pack's real ops DB — a tracked file — so tests would dirty the repo.
    monkeypatch.setattr(goal_audit, "LEDGER_DB_PATH", tmp_path / "ledger.db")
    eg.init()


def _goal(**kwargs):
    return eg.create_goal(kwargs.pop("statement", "Grow revenue ethically"), **kwargs)["goal"]


# --- the floor cannot be removed ---------------------------------------------


def test_every_goal_carries_the_baseline_constraints():
    goal = _goal(hard_constraints=[])

    kinds = {c["kind"] for c in goal["hard_constraints"]}
    assert eg.BASELINE_KINDS <= kinds
    assert goal["constraints_in_words"]  # readable, for the UI


def test_a_caller_cannot_weaken_a_baseline_rule():
    goal = _goal(
        hard_constraints=[{"kind": "no_deception", "rule": "Deception is fine if it helps sales"}]
    )

    rules = {c["kind"]: c["rule"] for c in goal["hard_constraints"]}
    assert rules["no_deception"] == next(
        c["rule"] for c in eg.BASELINE_CONSTRAINTS if c["kind"] == "no_deception"
    )
    assert "fine" not in rules["no_deception"]


def test_extra_constraints_are_kept_alongside_the_baseline():
    goal = _goal(hard_constraints=[{"kind": "no_weekend_contact", "rule": "Never contact on weekends"}])

    kinds = {c["kind"] for c in goal["hard_constraints"]}
    assert "no_weekend_contact" in kinds
    assert eg.BASELINE_KINDS <= kinds


def test_an_update_cannot_strip_the_baseline_either():
    goal = _goal()

    updated = eg.update_goal(goal["id"], hard_constraints=[])

    assert eg.BASELINE_KINDS <= {c["kind"] for c in updated["hard_constraints"]}


def test_a_goal_needs_a_statement():
    assert eg.create_goal("   ")["error"] == "goal_statement_required"


def test_autonomy_defaults_to_draft_only_and_rejects_nonsense():
    assert _goal()["soft_preferences"]["autonomy_level"] == "draft_only"
    reckless = _goal(soft_preferences={"autonomy_level": "do_anything"})
    assert reckless["soft_preferences"]["autonomy_level"] == "draft_only"


# --- CRUD --------------------------------------------------------------------


def test_crud_and_active_goal():
    goal = _goal(statement="Increase retention ethically")

    assert eg.get_goal(goal["id"])["statement"] == "Increase retention ethically"
    assert eg.active_goal()["id"] == goal["id"]
    assert eg.update_goal(goal["id"], statement="Increase retention safely")["statement"] == (
        "Increase retention safely"
    )
    assert eg.delete_goal(goal["id"]) is True
    assert eg.get_goal(goal["id"]) is None


def test_criteria_are_normalized():
    goal = _goal(measurable_criteria=[{"metric": "mrr", "direction": "sideways"}])

    criterion = goal["measurable_criteria"][0]
    assert criterion["direction"] == "increase"  # nonsense coerced, never stored raw
    assert criterion["name"] == "mrr"


# --- the ethical gate --------------------------------------------------------


def test_unknown_action_kinds_are_confirm_gated_not_assumed_safe():
    assert eg.classify_action("frobnicate_the_ledger") == eg.RISK_CONFIRM
    assert eg.classify_action(None) == eg.RISK_CONFIRM
    assert eg.classify_action("inspect") == eg.RISK_SAFE


def test_money_and_external_actions_never_run_on_their_own():
    goal = _goal(soft_preferences={"autonomy_level": "safe_auto"})

    for kind in ("send_message", "transfer_funds", "publish", "approve_app", "deploy"):
        verdict = eg.gate_action(goal, action_kind=kind)
        assert verdict["allowed"] is False, kind
        assert verdict["requires_confirm"] is True, kind


def test_safe_actions_run_only_when_autonomy_allows():
    drafting = _goal()
    assert eg.gate_action(drafting, action_kind="inspect")["allowed"] is False

    allowed = _goal(soft_preferences={"autonomy_level": "safe_auto"})
    assert eg.gate_action(allowed, action_kind="inspect")["allowed"] is True


def test_a_breached_constraint_blocks_even_a_safe_action():
    goal = _goal(soft_preferences={"autonomy_level": "safe_auto"})

    verdict = eg.gate_action(goal, action_kind="inspect", violates=["no_deception"])

    assert verdict["allowed"] is False
    assert verdict["requires_confirm"] is False  # not confirmable — forbidden
    assert "no_deception" in verdict["blocked_by"]
    assert "Not allowed" in verdict["reasons"][0]


def test_failed_predicates_block_regardless_of_action():
    goal = _goal(soft_preferences={"autonomy_level": "safe_auto"})

    verdict = eg.gate_action(
        goal, action_kind="inspect", predicate_results=[{"name": "x", "pass": False}]
    )

    assert verdict["allowed"] is False
    assert verdict["predicates_pass"] is False


# --- termination: confidence is never enough ---------------------------------


def test_high_confidence_with_failed_checks_is_caught_not_shipped():
    goal = _goal()

    out = eg.evaluate_termination(
        goal, collapse=0.99, predicate_results=[{"name": "revenue_up", "pass": False}]
    )

    assert out["success"] is False
    assert out["verdict"] == "false_pass_caught"


def test_a_constraint_breach_outranks_everything():
    goal = _goal()

    out = eg.evaluate_termination(
        goal,
        collapse=0.99,
        predicate_results=[{"name": "revenue_up", "pass": True}],
        violates=["no_illegal"],
    )

    assert out["success"] is False
    assert out["verdict"] == "constraint_violated"


def test_success_needs_passing_checks():
    goal = _goal()

    assert eg.evaluate_termination(
        goal, collapse=0.9, predicate_results=[{"name": "revenue_up", "pass": True}]
    )["verdict"] == "success"
    assert eg.evaluate_termination(goal, collapse=0.2, predicate_results=[])["verdict"] == "continue"


def test_seek_history_is_recorded_and_trimmed():
    goal = _goal()

    for i in range(25):
        eg.record_seek(goal["id"], [{"title": f"p{i}"}], ["because"], trigger="idle")

    seeks = eg.list_seeks(goal["id"], limit=50)
    assert len(seeks) == 20  # trimmed
    assert seeks[0]["proposals"][0]["title"] == "p24"
