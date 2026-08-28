"""gen-cFSM P0 gate tests — GENERATE→COMPILE dry-run, |V| ∈ {3,5,7}, cycle reject 100%."""

from __future__ import annotations

import pytest

from CortexOS.execution.gen_cfsm import (
    ALLOWED_HORIZONS,
    DECISION_AUDIT_FAIL,
    DECISION_CONTINUE,
    DECISION_FORCE_AUDIT,
    DECISION_REGENERATE,
    DECISION_TERMINATE,
    compile_ir,
    collapse_score,
    dry_run,
    generate_ir,
    route_step,
    run_collapse_loop,
    validate_ir,
)


@pytest.mark.parametrize("horizon", ALLOWED_HORIZONS)
def test_dry_run_compiles_all_allowed_horizons(horizon):
    report = dry_run("move pallet A3 to dock 2", horizon)

    assert report["ok"] is True
    assert report["errors"] == []
    assert report["node_count"] == horizon
    assert report["intent_hash"] == f"gen_cfsm_h{horizon}"


@pytest.mark.parametrize("horizon", [1, 2, 4, 6, 8, 9, 0, -3])
def test_horizons_outside_alphabet_rejected(horizon):
    ir = generate_ir("goal", 3)
    ir["horizon"] = horizon
    errors = validate_ir(ir)

    assert any(e.startswith("horizon_invalid") for e in errors)


def test_node_count_must_equal_horizon():
    ir = generate_ir("goal", 5)
    ir["nodes"] = ir["nodes"][:4]

    errors = validate_ir(ir)

    assert any(e.startswith("node_count_mismatch") for e in errors)


def _cycle_two():
    ir = generate_ir("goal", 3)
    ir["nodes"][0]["inputs"] = ["step_2"]  # step_1 ↔ step_2
    return ir


def _cycle_three():
    ir = generate_ir("goal", 5)
    ir["nodes"][0]["inputs"] = ["step_3"]  # 1→2→3→1
    return ir


def _self_loop():
    ir = generate_ir("goal", 3)
    ir["nodes"][1]["inputs"] = ["step_2"]
    return ir


def _back_edge_long():
    ir = generate_ir("goal", 7)
    ir["nodes"][1]["inputs"] = ["step_5"]  # back edge deep in the chain
    return ir


@pytest.mark.parametrize("make_ir", [_cycle_two, _cycle_three, _self_loop, _back_edge_long])
def test_cycles_rejected_100_percent(make_ir):
    compiled = compile_ir(make_ir())

    assert compiled["ok"] is False
    assert any("cycle_detected" in e or "self_loop" in e for e in compiled["errors"])


def test_kind_outside_alphabet_rejected():
    ir = generate_ir("goal", 3)
    ir["nodes"][0]["kind"] = "INFER_REMOTE"

    errors = validate_ir(ir)

    assert any(e.startswith("kind_not_in_alphabet") for e in errors)


def test_exactly_one_emit_required():
    ir = generate_ir("goal", 3)
    ir["nodes"][0]["kind"] = "EMIT"  # two EMITs now
    assert any(e.startswith("emit_count_invalid") for e in validate_ir(ir))

    ir2 = generate_ir("goal", 3)
    ir2["nodes"][-1]["kind"] = "document_ref"
    ir2["nodes"][-1]["context_key"] = "prompt"
    assert any(e.startswith("emit_count_invalid") for e in validate_ir(ir2))


def test_unknown_input_rejected():
    ir = generate_ir("goal", 3)
    ir["nodes"][1]["inputs"] = ["ghost"]

    assert any(e.startswith("input_unknown") for e in validate_ir(ir))


def test_compiled_program_reaches_real_parser():
    compiled = compile_ir(generate_ir("goal", 3))

    assert compiled["ok"] is True
    program = compiled["program"]
    assert program.output_node_id == "audit"
    assert len(program.nodes) == 3


# --- collapse router decision table -----------------------------------------


def test_route_terminates_on_collapse_and_predicates():
    out = route_step(
        collapse=0.9, prev_collapse=0.5, predicates_pass=True, step_count=1, horizon=5
    )
    assert out["decision"] == DECISION_TERMINATE


def test_route_audit_fail_on_collapse_without_predicates():
    out = route_step(
        collapse=0.9, prev_collapse=0.5, predicates_pass=False, step_count=1, horizon=5
    )
    assert out["decision"] == DECISION_AUDIT_FAIL


def test_route_continue_on_progress():
    out = route_step(
        collapse=0.4, prev_collapse=0.2, predicates_pass=False, step_count=1, horizon=5
    )
    assert out["decision"] == DECISION_CONTINUE
    assert out["stall_count"] == 0


def test_route_regenerates_after_k_stalls():
    first = route_step(
        collapse=0.4, prev_collapse=0.4, predicates_pass=False, step_count=1, horizon=5
    )
    assert first["decision"] == DECISION_CONTINUE
    assert first["stall_count"] == 1

    second = route_step(
        collapse=0.4,
        prev_collapse=0.4,
        predicates_pass=False,
        step_count=2,
        horizon=5,
        stall_count=first["stall_count"],
    )
    assert second["decision"] == DECISION_REGENERATE


def test_route_forces_audit_at_horizon():
    out = route_step(
        collapse=0.99, prev_collapse=0.9, predicates_pass=True, step_count=5, horizon=5
    )
    assert out["decision"] == DECISION_FORCE_AUDIT


def test_collapse_score_identity():
    assert collapse_score([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert collapse_score([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_collapse_loop_terminates_on_goal():
    goal = [1.0, 0.0, 0.0]
    states = [[0.2, 1.0, 0.0], [0.6, 0.4, 0.0], [0.99, 0.05, 0.0]]

    result = run_collapse_loop(goal, states, lambda step: True, horizon=5)

    assert result["final"] == DECISION_TERMINATE
    assert result["steps"] == 3


def test_collapse_loop_forces_audit_when_never_converging():
    goal = [1.0, 0.0]
    states = [[0.0, 1.0], [0.05, 1.0], [0.1, 1.0]]

    result = run_collapse_loop(goal, states, lambda step: False, horizon=3)

    assert result["final"] in (DECISION_FORCE_AUDIT, DECISION_REGENERATE)
