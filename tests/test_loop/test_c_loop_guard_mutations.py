"""C-LOOP (#291) parent-fail proofs: each must-fail fails with its guard removed.

Each case reruns the named must-fail tests in a subprocess with one guard
switched off (tests/test_loop/c_loop_guard_off.py) and asserts they FAIL, then
that the same tests pass with the guard on. A must-fail that still passes
without its guard is not testing the guard.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UNIT = "tests/test_loop/test_c_loop_orchestrator.py"
SANDBOX = "tests/test_loop/test_c_loop_sandbox.py"
HTTP = "tests/dms/test_c_loop_contract_ask.py"

CASES = {
    "evaluate": (
        [
            f"{UNIT}::test_must_fail_wrong_answer_caught_by_evaluate",
            f"{HTTP}::test_must_fail_contract_ask_wrong_answer_becomes_abstain",
        ],
        [
            "test_must_fail_wrong_answer_caught_by_evaluate",
            "test_must_fail_contract_ask_wrong_answer_becomes_abstain",
        ],
    ),
    "check": (
        [f"{UNIT}::test_must_fail_wrong_answer_caught_by_check"],
        [
            "test_must_fail_wrong_answer_caught_by_check[ungranted_table]",
            "test_must_fail_wrong_answer_caught_by_check[empty_result]",
            "test_must_fail_wrong_answer_caught_by_check[null_result]",
            "test_must_fail_wrong_answer_caught_by_check[non_finite]",
            "test_must_fail_wrong_answer_caught_by_check[unanalysable]",
        ],
    ),
    "named_reason": (
        [f"{UNIT}::test_must_fail_abstain_without_named_reason"],
        ["test_must_fail_abstain_without_named_reason"],
    ),
    "scored_pack": (
        [
            f"{UNIT}::test_must_fail_scored_round_loop_writes_no_memory",
            f"{HTTP}::test_loop_scored_round_writes_no_memory",
        ],
        [
            "test_must_fail_scored_round_loop_writes_no_memory",
            "test_loop_scored_round_writes_no_memory",
        ],
    ),
    "sandbox": (
        [
            f"{SANDBOX}::test_must_fail_sandbox_refuses_network",
            f"{SANDBOX}::test_must_fail_sandbox_refuses_swallowed_network_attempt",
            f"{SANDBOX}::test_must_fail_sandbox_refuses_source_handle_input",
            f"{SANDBOX}::test_must_fail_sandbox_refuses_opening_a_database",
            f"{SANDBOX}::test_must_fail_sandbox_refuses_write_outside_scratch",
        ],
        [
            "test_must_fail_sandbox_refuses_network",
            "test_must_fail_sandbox_refuses_swallowed_network_attempt",
            "test_must_fail_sandbox_refuses_source_handle_input",
            "test_must_fail_sandbox_refuses_opening_a_database",
            "test_must_fail_sandbox_refuses_write_outside_scratch",
        ],
    ),
}


def _pytest(node_ids: list[str], guard_off: str | None, warehouse: Path) -> subprocess.CompletedProcess[str]:
    # This process may hold the warehouse's DuckDB lock; the child gets its own copy.
    env = {**os.environ, "DMS_WAREHOUSE_DB": str(warehouse)}
    args = [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", *node_ids]
    if guard_off:
        env["C_LOOP_GUARD_OFF"] = guard_off
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "tests" / "test_loop"), env.get("PYTHONPATH", "")]
        )
        args[3:3] = ["-p", "c_loop_guard_off"]
    return subprocess.run(
        args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=300, check=False
    )


@pytest.mark.parametrize("guard", sorted(CASES))
def test_must_fail_tests_fail_without_their_guard(guard: str, tmp_path: Path) -> None:
    node_ids, must_fail = CASES[guard]
    source = Path(os.environ.get("DMS_WAREHOUSE_DB") or ROOT / "data" / "dms_demo.duckdb")
    warehouse = tmp_path / "warehouse.duckdb"
    if source.exists():
        shutil.copyfile(source, warehouse)
    off = _pytest(node_ids, guard, warehouse)
    failed = [line for line in off.stdout.splitlines() if line.startswith("FAILED ")]
    assert off.returncode == 1, off.stdout + off.stderr
    for name in must_fail:
        assert any(name in line for line in failed), (
            f"{name} passed with guard {guard} off:\n{off.stdout}"
        )

    on = _pytest(node_ids, None, warehouse)
    assert on.returncode == 0, on.stdout + on.stderr
