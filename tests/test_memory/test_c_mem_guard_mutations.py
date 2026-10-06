"""C-MEM (#290) parent-fail proofs: each must-fail fails with its guard removed.

Each case reruns the named must-fail tests in a subprocess with one guard
switched off (tests/test_memory/c_mem_guard_off.py) and asserts they FAIL,
then that the same tests pass with the guard on. A must-fail that still passes
without its guard is not testing the guard.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UNIT = "tests/test_memory/test_c_mem_space_memory.py"
HTTP = "tests/dms/test_c_mem_contract_ask.py"

CASES = {
    "scored_pack": (
        [
            f"{UNIT}::test_must_fail_scored_pack_write_refused_curated_ceo",
            f"{UNIT}::test_must_fail_scored_pack_write_refused",
        ],
        [
            "test_must_fail_scored_pack_write_refused_curated_ceo",
            "test_must_fail_scored_pack_write_refused[",
        ],
    ),
    "space_isolation": (
        [
            f"{UNIT}::test_must_fail_space_b_never_reads_space_a",
            f"{HTTP}::test_must_fail_contract_ask_space_b_never_reads_space_a_solution",
        ],
        [
            "test_must_fail_space_b_never_reads_space_a[table]",
            "test_must_fail_space_b_never_reads_space_a[formula]",
            "test_must_fail_space_b_never_reads_space_a[tool]",
            "test_must_fail_space_b_never_reads_space_a[solution]",
            "test_must_fail_contract_ask_space_b_never_reads_space_a_solution",
        ],
    ),
    "reused_validation": (
        [f"{UNIT}::test_reuse_reruns_sql_on_current_data_and_is_never_a_validation"],
        ["test_reuse_reruns_sql_on_current_data_and_is_never_a_validation"],
    ),
}


def _pytest(node_ids: list[str], guard_off: str | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    args = [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", *node_ids]
    if guard_off:
        env["C_MEM_GUARD_OFF"] = guard_off
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "tests" / "test_memory"), env.get("PYTHONPATH", "")]
        )
        args[3:3] = ["-p", "c_mem_guard_off"]
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
