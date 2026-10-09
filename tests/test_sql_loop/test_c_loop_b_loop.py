"""C-LOOP-B (#304): the self-correcting SQL loop and its wire record.

The generator is a mock; the gate and executor are fakes that record every
call. No model, no database. HTTP coverage is in
tests/dms/test_c_loop_b_contract_ask.py.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.answer import (
    SQL_LOOP_ABSTAIN_REASONS,
    AbstainReason,
    Answer,
    SqlAttempt,
    SqlLoop,
    SqlLoopCheck,
)
from pydantic import ValidationError

from CortexOS.dms import sql_self_correct as loop_mod
from CortexOS.dms.sql_self_correct import CheckContext, run_sql_loop
from CortexOS.dms.sql_validate_gate import ValidateGateResult

ROOT = Path(__file__).resolve().parents[2]
Q = "how many transactions are there"
GOOD = "SELECT COUNT(*) AS txn_count FROM transactions"
GOOD_ROWS = [{"txn_count": 5001}]

#: SQL -> rows the fake executor returns. A value that is an exception is raised.
RESULTS: dict[str, Any] = {
    GOOD: GOOD_ROWS,
    "ERR": RuntimeError("Binder Error: column 'secret_value' not found"),
    "EMPTY": [],
    "NULLS": [{"txn_count": None}],
    "SHAPE": [{"a": 1}, {"b": 2}],
    "TYPE": [{"sku": "A", "kg": 1.0}, {"sku": "B", "kg": "n/a"}],
    "NEG": [{"txn_count": -3}],
    "SUM": [{"sku": "A", "kg": 1.0}, {"sku": "B", "kg": 2.0}, {"sku": "Total", "kg": 9.0}],
    "FANOUT": [{"sku": f"S{i}"} for i in range(7)],
    "LISTING": [{"txn_count": 1}, {"txn_count": 2}],
}

#: Bad SQL key -> (check that must catch it, named abstain reason).
BAD = {
    "ERR": (SqlLoopCheck.EXECUTE, AbstainReason.SQL_ERROR_AFTER_RETRIES),
    "DROP TABLE transactions": (SqlLoopCheck.GATE, AbstainReason.SQL_GATE_REFUSED_AFTER_RETRIES),
    "EMPTY": (SqlLoopCheck.EMPTY, AbstainReason.EMPTY_RESULT_AFTER_RETRIES),
    "NULLS": (SqlLoopCheck.EMPTY, AbstainReason.EMPTY_RESULT_AFTER_RETRIES),
    "SHAPE": (SqlLoopCheck.SHAPE, AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES),
    "TYPE": (SqlLoopCheck.WRONG_TYPE, AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES),
    "NEG": (SqlLoopCheck.NEGATIVE_COUNT, AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES),
    "SUM": (SqlLoopCheck.SUM_NOT_RECONCILED, AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES),
    "FANOUT": (SqlLoopCheck.ROWS_BEYOND_GRANT, AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES),
    "LISTING": (SqlLoopCheck.PLAUSIBILITY, AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES),
}
IMPLAUSIBLE = ["EMPTY", "NULLS", "SHAPE", "TYPE", "NEG", "SUM", "FANOUT", "LISTING"]


class Harness:
    """Mock generator plus a recording gate and executor."""

    def __init__(self, script: Sequence[str | None | Exception]) -> None:
        self.script = list(script)
        self.prompts: list[tuple[SqlAttempt, ...]] = []
        self.gated: list[str] = []
        self.passed: list[ValidateGateResult] = []
        self.executed: list[str] = []

    def generate(self, question: str, prior: Sequence[SqlAttempt]) -> str | None:
        assert question == Q
        self.prompts.append(tuple(prior))
        item = self.script[min(len(self.prompts), len(self.script)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    def gate(self, sql: str) -> ValidateGateResult:
        self.gated.append(sql)
        if sql.upper().startswith("DROP"):
            return ValidateGateResult(passed=False, violations=["DDL_ATTEMPT"], attempts=1)
        result = ValidateGateResult(passed=True, safe_sql=sql, source_sql=sql, attempts=1)
        self.passed.append(result)
        return result

    def execute(self, gated: ValidateGateResult) -> list[dict[str, Any]]:
        assert any(gated is p for p in self.passed), "executor got a result the gate did not pass"
        sql = gated.source_sql or ""
        self.executed.append(sql)
        out = RESULTS[sql]
        if isinstance(out, Exception):
            raise out
        return [dict(r) for r in out]

    def run(self, ctx: CheckContext | None = None):
        return run_sql_loop(
            Q,
            generate=self.generate,
            gate=self.gate,
            execute=self.execute,
            ctx=ctx or CheckContext(row_ceiling=lambda sql: 5),
        )


def _attempt(n: int, check: SqlLoopCheck, outcome: str, sql: str | None = "S") -> SqlAttempt:
    return SqlAttempt(
        served_attempt=n,
        served_sql=sql,
        served_check=check,
        served_outcome=outcome,  # type: ignore[arg-type]
        served_at="2026-10-06T00:00:00+00:00",
    )


# --- answered ---------------------------------------------------------------


def test_first_attempt_passing_answers_without_retry() -> None:
    h = Harness([GOOD])
    result = h.run()
    assert result.answered
    assert result.rows == GOOD_ROWS
    assert result.sql == GOOD
    rec = result.record
    assert rec.served_retries == 0
    assert [(a.served_attempt, a.served_check, a.served_outcome) for a in rec.served_attempts] == [
        (1, SqlLoopCheck.PASSED, "answered")
    ]


def test_error_then_empty_then_good_is_corrected_with_feedback() -> None:
    h = Harness(["ERR", "EMPTY", GOOD])
    result = h.run()
    assert result.answered and result.rows == GOOD_ROWS
    rec = result.record
    assert rec.served_retries == 2
    assert [a.served_check for a in rec.served_attempts] == [
        SqlLoopCheck.EXECUTE,
        SqlLoopCheck.EMPTY,
        SqlLoopCheck.PASSED,
    ]
    assert [a.served_outcome for a in rec.served_attempts] == ["retry", "retry", "answered"]
    # Feedback is the stamped attempts so far: SQL, check and error, oldest first.
    assert [len(p) for p in h.prompts] == [0, 1, 2]
    assert h.prompts[2][0].served_sql == "ERR"
    assert h.prompts[2][0].served_check == SqlLoopCheck.EXECUTE
    assert h.prompts[2][1].served_check == SqlLoopCheck.EMPTY


def test_every_attempt_is_stamped_served() -> None:
    result = Harness(["ERR", "NEG", GOOD]).run()
    for n, a in enumerate(result.record.served_attempts, start=1):
        dumped = a.model_dump()
        assert set(dumped) == {
            "served_attempt",
            "served_sql",
            "served_check",
            "served_error",
            "served_outcome",
            "served_at",
        }
        assert a.served_attempt == n and a.served_at and a.served_sql
    assert result.record.served_attempts[0].served_error
    assert result.record.served_attempts[1].served_error == "count column 'txn_count' has a negative value"


def test_feedback_never_carries_result_values() -> None:
    result = Harness(["ERR", "TYPE", "SUM"]).run()
    errors = " ".join(a.served_error or "" for a in result.record.served_attempts)
    assert "secret_value" not in errors  # quoted literal in the SQL error is masked
    assert "'?'" in errors
    assert "n/a" not in errors and "9.0" not in errors


# --- must-fail (1): a WRONG or implausible result is never served -----------


@pytest.mark.parametrize("bad", IMPLAUSIBLE)
def test_must_fail_implausible_result_never_served(bad: str) -> None:
    h = Harness([bad])
    result = h.run()
    check, reason = BAD[bad]
    assert not result.answered
    assert result.rows is None and result.sql is None
    assert result.record.served_abstain_reason == reason
    assert {a.served_check for a in result.record.served_attempts} == {check}


@pytest.mark.parametrize("bad", IMPLAUSIBLE)
def test_must_fail_implausible_then_corrected_serves_only_corrected_rows(bad: str) -> None:
    result = Harness([bad, GOOD]).run()
    assert result.answered
    assert result.rows == GOOD_ROWS and result.sql == GOOD
    assert result.record.served_attempts[0].served_check == BAD[bad][0]


def test_must_fail_answer_refuses_rows_on_abstained_loop() -> None:
    abstained = SqlLoop(
        served_outcome="abstained",
        served_abstain_reason=AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES,
        served_retries=0,
        served_attempts=[_attempt(1, SqlLoopCheck.NEGATIVE_COUNT, "abstained")],
    )
    base = {
        "answer": "x",
        "audit_id": "a",
        "route": "abstain",
        "provenance": {"layer": "abstain", "badge": "abstain"},
        "sql_loop": abstained,
    }
    Answer.model_validate(base)
    for bad in (
        {"rows": [{"txn_count": -3}]},
        {"sql_used": "SELECT -3"},
        {"provenance": {"layer": "sql_self_correct", "badge": "session"}},
    ):
        with pytest.raises(ValidationError, match="abstained SQL loop serves no rows"):
            Answer.model_validate({**base, **bad})


# --- must-fail (2): an abstain without a named reason fails ------------------


@pytest.mark.parametrize("bad", sorted(BAD))
def test_must_fail_every_abstain_names_its_reason(bad: str) -> None:
    result = Harness([bad]).run()
    assert result.record.served_outcome == "abstained"
    assert result.record.served_abstain_reason == BAD[bad][1]
    assert result.record.served_abstain_reason in SQL_LOOP_ABSTAIN_REASONS


@pytest.mark.parametrize(
    ("script", "reason", "check"),
    [
        ([None], AbstainReason.NO_SQL_CANDIDATE, SqlLoopCheck.NO_CANDIDATE),
        ([""], AbstainReason.NO_SQL_CANDIDATE, SqlLoopCheck.NO_CANDIDATE),
        ([TimeoutError("route down")], AbstainReason.SQL_GENERATOR_FAILED, SqlLoopCheck.GENERATOR_ERROR),
    ],
)
def test_generator_failure_is_a_named_abstain_without_retry(
    script: list[Any], reason: AbstainReason, check: SqlLoopCheck
) -> None:
    h = Harness(script)
    result = h.run()
    assert result.record.served_abstain_reason == reason
    assert [a.served_check for a in result.record.served_attempts] == [check]
    assert h.gated == [] and h.executed == []


def test_unregistered_generator_is_a_named_abstain() -> None:
    rec = loop_mod.unavailable().record
    assert rec.served_abstain_reason == AbstainReason.SQL_GENERATOR_UNAVAILABLE
    assert rec.served_attempts == []


@pytest.mark.parametrize(
    "reason",
    [None, AbstainReason.NO_TRUSTWORTHY_PATH, AbstainReason.POLICY_BLOCK, AbstainReason.NEEDS_CLARIFICATION],
)
def test_must_fail_abstain_without_named_reason_is_refused(reason: AbstainReason | None) -> None:
    with pytest.raises(ValidationError, match="needs a named reason"):
        SqlLoop(
            served_outcome="abstained",
            served_abstain_reason=reason,
            served_retries=0,
            served_attempts=[_attempt(1, SqlLoopCheck.EMPTY, "abstained")],
        )


def test_must_fail_answer_with_unnamed_loop_abstain_is_refused() -> None:
    with pytest.raises(ValidationError, match="needs a named reason"):
        Answer.model_validate(
            {
                "answer": "x",
                "audit_id": "a",
                "route": "abstain",
                "provenance": {"layer": "abstain", "badge": "abstain"},
                "sql_loop": {
                    "served_outcome": "abstained",
                    "served_retries": 0,
                    "served_attempts": [],
                },
            }
        )


def test_answered_record_must_end_on_a_passed_attempt() -> None:
    with pytest.raises(ValidationError, match="passed every check"):
        SqlLoop(
            served_outcome="answered",
            served_retries=0,
            served_attempts=[_attempt(1, SqlLoopCheck.NEGATIVE_COUNT, "answered")],
        )


# --- must-fail (3): never more than 2 retries --------------------------------


@pytest.mark.parametrize("bad", sorted(BAD))
def test_must_fail_never_more_than_two_retries(bad: str) -> None:
    h = Harness([bad, bad, bad, bad, bad, GOOD])
    result = h.run()
    assert len(h.prompts) == 3, "generator called more than first try + 2 retries"
    assert not result.answered
    assert result.record.served_retries == 2
    assert [a.served_attempt for a in result.record.served_attempts] == [1, 2, 3]
    assert [a.served_outcome for a in result.record.served_attempts] == ["retry", "retry", "abstained"]


def test_must_fail_record_with_three_retries_is_refused() -> None:
    attempts = [_attempt(n, SqlLoopCheck.EMPTY, "retry") for n in (1, 2, 3)]
    attempts.append(_attempt(4, SqlLoopCheck.EMPTY, "abstained"))
    with pytest.raises(ValidationError, match="at most 2"):
        SqlLoop(
            served_outcome="abstained",
            served_abstain_reason=AbstainReason.EMPTY_RESULT_AFTER_RETRIES,
            served_retries=3,
            served_attempts=attempts,
        )


# --- must-fail (4): a retry never bypasses the SQL gate ----------------------


def test_must_fail_retry_never_bypasses_gate() -> None:
    h = Harness(["ERR", "DROP TABLE transactions", GOOD])
    result = h.run()
    assert result.answered
    assert h.gated == ["ERR", "DROP TABLE transactions", GOOD]
    assert h.executed == ["ERR", GOOD]
    assert "DROP TABLE transactions" not in h.executed
    assert result.record.served_attempts[1].served_check == SqlLoopCheck.GATE
    assert result.record.served_attempts[1].served_error == "DDL_ATTEMPT"


def test_must_fail_gate_refused_every_time_never_executes() -> None:
    h = Harness(["DROP TABLE transactions"])
    result = h.run()
    assert h.gated == ["DROP TABLE transactions"] * 3
    assert h.executed == []
    assert result.record.served_abstain_reason == AbstainReason.SQL_GATE_REFUSED_AFTER_RETRIES


# --- wire stays 1.4-shaped when the loop did not run --------------------------


def test_answer_without_loop_has_no_sql_loop_key() -> None:
    answer = Answer.model_validate(
        {"answer": "x", "audit_id": "a", "route": "sql", "provenance": {"layer": "l", "badge": "session"}}
    )
    assert "sql_loop" not in answer.model_dump()
    assert "sql_loop" not in answer.model_dump_json()


# --- no scored-pack memory writes: the loop has no memory path at all --------


@pytest.mark.parametrize(
    "path", ["CortexOS/dms/sql_self_correct.py", "CortexOS/dms/sql_self_correct_ask.py"]
)
def test_loop_modules_never_touch_memory(path: str) -> None:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(m.startswith("CortexOS.memory") for m in imported), imported
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"write", "confirm_solution", "check_solution", "get_space_memory"}


def test_loop_modules_pick_no_model() -> None:
    for path in ("CortexOS/dms/sql_self_correct.py", "CortexOS/dms/sql_self_correct_ask.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        for banned in ("freeroute", "litellm", "httpx", "openai", "groq", "packs."):
            assert banned not in text.lower(), (path, banned)
