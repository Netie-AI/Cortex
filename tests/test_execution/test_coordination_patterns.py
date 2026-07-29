"""Coordination patterns + generator-verifier (Anthropic multi-agent distill)."""

from __future__ import annotations

import pytest

from CortexOS.execution import coordination_patterns as cp
from CortexOS.execution.generator_verifier import (
    build_revision_prompt,
    criteria_block,
    normalize_criteria,
    parse_verifier_payload,
    run_generator_verifier,
)


def test_catalog_has_six_entries():
    cat = cp.catalog()
    ids = {p["id"] for p in cat}
    assert ids == {
        "single_agent",
        "generator_verifier",
        "orchestrator_subagent",
        "agent_teams",
        "message_bus",
        "shared_state",
    }
    assert any(p["cortex_status"] == "strong" and p["id"] == "orchestrator_subagent" for p in cat)


def test_recommend_single_by_default():
    rec = cp.recommend({})
    assert rec.pattern == "single_agent"
    assert rec.multi_agent_justified is False


def test_recommend_generator_verifier():
    rec = cp.recommend({"quality_critical": True, "explicit_criteria": True})
    assert rec.pattern == "generator_verifier"
    assert rec.multi_agent_justified is True


def test_recommend_orchestrator_on_parallel():
    rec = cp.recommend(
        {
            "parallel_independent": True,
            "clear_decomposition": True,
            "bounded_subtasks": True,
        }
    )
    assert rec.pattern == "orchestrator_subagent"


def test_recommend_agent_teams_parked():
    rec = cp.recommend(
        {
            "parallel_independent": True,
            "long_running_workers": True,
            "bounded_subtasks": False,
        }
    )
    assert rec.pattern == "agent_teams"
    assert any("parked" in w.lower() for w in rec.warnings)


def test_recommend_from_prompt_research():
    rec = cp.recommend_from_prompt("Please research market trends in parallel across regions")
    assert rec.pattern == "orchestrator_subagent"
    assert rec.multi_agent_justified is True


def test_gap_matrix():
    matrix = cp.gap_matrix()
    assert len(matrix) == 6
    by_id = {m["pattern"]: m for m in matrix}
    assert by_id["orchestrator_subagent"]["status"] == "strong"
    assert by_id["agent_teams"]["parked"] is True


def test_normalize_criteria_rejects_empty():
    with pytest.raises(ValueError, match="explicit criteria"):
        normalize_criteria([])


def test_parse_verifier_refuted_and_confirmed():
    fail = parse_verifier_payload(
        {"refuted": True, "reason": "circular source"},
        criteria=["independent_source"],
    )
    assert fail.passed is False
    ok = parse_verifier_payload(
        {
            "confirmed": True,
            "reason": "read file",
            "criteria_checked": ["code_matches_claim", "fix_would_change_behaviour", "not_already_handled"],
        },
        criteria=["code_matches_claim", "fix_would_change_behaviour", "not_already_handled"],
    )
    assert ok.passed is True
    assert ok.early_victory_risk is False


def test_early_victory_forces_reject_path():
    rubber = parse_verifier_payload(
        {"passed": True, "reason": "looks good"},
        criteria=["a", "b", "c"],
    )
    assert rubber.passed is True
    assert rubber.early_victory_risk is True


@pytest.mark.asyncio
async def test_generator_verifier_loop_converges():
    calls = {"n": 0}

    async def generate(prompt: str, attempt: int) -> str:
        calls["n"] += 1
        if attempt == 1:
            return "draft-v1 missing criterion B"
        return "draft-v2 satisfies A and B"

    async def verify(output: str, criteria, attempt: int):
        if "v2" in output:
            return {
                "passed": True,
                "reason": "all good",
                "criteria_checked": list(criteria),
            }
        return {"passed": False, "reason": "missing B", "criteria_checked": ["A"]}

    out = await run_generator_verifier(
        task="Write the thing",
        criteria=["A", "B"],
        generate=generate,
        verify=verify,
        max_attempts=3,
        fallback="return_best_with_caveats",
    )
    assert out.status == "accepted"
    assert out.attempts == 2
    assert "v2" in out.output
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_generator_verifier_max_attempts_fallback():
    async def generate(prompt: str, attempt: int) -> str:
        return f"bad-{attempt}"

    async def verify(output: str, criteria, attempt: int):
        return {"passed": False, "reason": "still wrong"}

    out = await run_generator_verifier(
        task="task",
        criteria=["must be correct"],
        generate=generate,
        verify=verify,
        max_attempts=2,
        fallback="escalate_human",
    )
    assert out.status == "max_attempts"
    assert out.fallback == "escalate_human"
    assert out.attempts == 2
    assert out.caveats


def test_revision_and_criteria_helpers():
    prompt = build_revision_prompt("Do X", "old", "fix Y", 1)
    assert "verifier feedback" in prompt
    assert "fix Y" in prompt
    block = criteria_block(["a", "b"])
    assert "1. a" in block
    assert "ALL of these criteria" in block
