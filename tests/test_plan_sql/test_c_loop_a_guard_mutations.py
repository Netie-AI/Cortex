"""C-LOOP-A (#303) guard-removed proofs: each must-fail fails without its guard.

Each case reruns the named must-fail tests in a subprocess with one guard
switched off (tests/test_plan_sql/plan_sql_guard_off.py) and asserts they FAIL,
then that the same tests pass with the guard on. A must-fail that still passes
without its guard is not testing the guard. The import-contract must-fail is
proven the same way by its own clean-tree control
(test_c_loop_a_import_contract.py::test_import_contract_keeps_on_clean_tree).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HTTP = "tests/test_plan_sql/test_c_loop_a_plan_sql.py"

CASES = {
    "freeroute_only": (
        [f"{HTTP}::test_must_fail_direct_provider_call_is_refused_not_executed"],
        ["test_must_fail_direct_provider_call_is_refused_not_executed"],
    ),
    "sql_gate": (
        [
            f"{HTTP}::test_must_fail_gate_violating_sql_is_refused_not_executed",
            f"{HTTP}::test_must_fail_unknown_column_retries_and_does_not_execute_it",
        ],
        [
            "test_must_fail_gate_violating_sql_is_refused_not_executed[file_read_function]",
            "test_must_fail_gate_violating_sql_is_refused_not_executed[table_outside_grant]",
            "test_must_fail_unknown_column_retries_and_does_not_execute_it",
        ],
    ),
    "payload_grant": (
        [f"{HTTP}::test_must_fail_payload_cannot_widen_the_signed_grant"],
        [
            "test_must_fail_payload_cannot_widen_the_signed_grant[extra_table]",
            "test_must_fail_payload_cannot_widen_the_signed_grant[join_to_table_outside_grant]",
            "test_must_fail_payload_cannot_widen_the_signed_grant[join_outside_payload]",
        ],
    ),
    "payload_scope": (
        [f"{HTTP}::test_must_fail_sql_outside_payload_is_refused_even_inside_grant"],
        ["test_must_fail_sql_outside_payload_is_refused_even_inside_grant"],
    ),
}


def _pytest(node_ids: list[str], guard_off: str | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    args = [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", *node_ids]
    if guard_off:
        env["PLAN_SQL_GUARD_OFF"] = guard_off
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "tests" / "test_plan_sql"), env.get("PYTHONPATH", "")]
        )
        args[3:3] = ["-p", "plan_sql_guard_off"]
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
