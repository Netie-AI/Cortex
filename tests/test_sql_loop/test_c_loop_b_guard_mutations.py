"""C-LOOP-B (#304) parent-fail proofs: each must-fail fails with its guard removed.

Each case reruns the named must-fail tests in a subprocess with one guard
switched off (tests/test_sql_loop/c_loop_b_guard_off.py) and asserts they
FAIL, then that the same tests pass with the guard on. A must-fail that still
passes without its guard is not testing the guard.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UNIT = "tests/test_sql_loop/test_c_loop_b_loop.py"
HTTP = "tests/dms/test_c_loop_b_contract_ask.py"

CASES = {
    # (1) a WRONG or implausible result is never served
    "result_checks": (
        [
            f"{UNIT}::test_must_fail_implausible_result_never_served",
            f"{HTTP}::test_must_fail_contract_ask_never_serves_implausible_rows",
        ],
        [
            *(
                f"test_must_fail_implausible_result_never_served[{bad}]"
                for bad in ("EMPTY", "NULLS", "SHAPE", "TYPE", "NEG", "SUM", "FANOUT", "LISTING")
            ),
            "test_must_fail_contract_ask_never_serves_implausible_rows",
        ],
    ),
    "abstained_rows": (
        [f"{UNIT}::test_must_fail_answer_refuses_rows_on_abstained_loop"],
        ["test_must_fail_answer_refuses_rows_on_abstained_loop"],
    ),
    # (2) an abstain without a named reason fails
    "named_reason": (
        [
            f"{UNIT}::test_must_fail_abstain_without_named_reason_is_refused",
            f"{UNIT}::test_must_fail_answer_with_unnamed_loop_abstain_is_refused",
        ],
        [
            "test_must_fail_abstain_without_named_reason_is_refused[None]",
            "test_must_fail_abstain_without_named_reason_is_refused[no_trustworthy_path]",
            "test_must_fail_abstain_without_named_reason_is_refused[policy_block]",
            "test_must_fail_abstain_without_named_reason_is_refused[needs_clarification]",
            "test_must_fail_answer_with_unnamed_loop_abstain_is_refused",
        ],
    ),
    # (3) never more than 2 retries
    "loop_retry_cap": (
        [
            f"{UNIT}::test_must_fail_never_more_than_two_retries",
            f"{HTTP}::test_must_fail_contract_ask_never_more_than_two_retries",
        ],
        [
            "test_must_fail_never_more_than_two_retries[EMPTY]",
            "test_must_fail_never_more_than_two_retries[DROP TABLE transactions]",
            "test_must_fail_contract_ask_never_more_than_two_retries",
        ],
    ),
    "wire_retry_cap": (
        [f"{UNIT}::test_must_fail_record_with_three_retries_is_refused"],
        ["test_must_fail_record_with_three_retries_is_refused"],
    ),
    # (4) a retry never bypasses the SQL gate
    "sql_gate": (
        [
            f"{UNIT}::test_must_fail_retry_never_bypasses_gate",
            f"{UNIT}::test_must_fail_gate_refused_every_time_never_executes",
            f"{HTTP}::test_must_fail_contract_ask_retry_never_bypasses_sql_gate",
            f"{HTTP}::test_must_fail_contract_ask_gate_refusals_are_never_executed",
        ],
        [
            "test_must_fail_retry_never_bypasses_gate",
            "test_must_fail_gate_refused_every_time_never_executes",
            "test_must_fail_contract_ask_retry_never_bypasses_sql_gate",
            "test_must_fail_contract_ask_gate_refusals_are_never_executed",
        ],
    ),
}


def _pytest(node_ids: list[str], guard_off: str | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    args = [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", *node_ids]
    if guard_off:
        env["C_LOOP_B_GUARD_OFF"] = guard_off
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "tests" / "test_sql_loop"), env.get("PYTHONPATH", "")]
        )
        args[3:3] = ["-p", "c_loop_b_guard_off"]
    return subprocess.run(
        args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=300, check=False
    )


@pytest.mark.parametrize("guard", sorted(CASES))
def test_must_fail_tests_fail_without_their_guard(guard: str) -> None:
    node_ids, must_fail = CASES[guard]
    off = _pytest(node_ids, guard)
    failed = [line for line in off.stdout.splitlines() if line.startswith("FAILED ")]
    assert off.returncode == 1, off.stdout + off.stderr
    for name in must_fail:
        assert any(name in line for line in failed), (
            f"{name} passed with guard {guard} off:\n{off.stdout}"
        )

    on = _pytest(node_ids, None)
    assert on.returncode == 0, on.stdout + on.stderr
