"""C-LOOP-C (#305) guard-removed proofs: each must-fail fails without its guard.

Each case reruns the named must-fail tests in a subprocess with one guard
switched off (tests/dms/c_loop_c_guard_off.py) and asserts they FAIL, then
that the same tests pass with the guard on. A must-fail that still passes
without its guard is not testing the guard.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UNIT = "tests/dms/test_c_loop_c_result_package.py"

CASES = {
    "insight_numbers": (
        [f"{UNIT}::test_must_fail_insight_number_not_in_rows_is_rejected"],
        [
            "test_must_fail_insight_number_not_in_rows_is_rejected[Sales grew 12% this quarter.]",
            "test_must_fail_insight_number_not_in_rows_is_rejected[4 rows returned.]",
        ],
    ),
    "chart_fields": (
        [f"{UNIT}::test_must_fail_chart_spec_column_not_in_rows_is_rejected"],
        [
            "test_must_fail_chart_spec_column_not_in_rows_is_rejected[spec0]",
            "test_must_fail_chart_spec_column_not_in_rows_is_rejected[spec1]",
            "test_must_fail_chart_spec_column_not_in_rows_is_rejected[spec2]",
        ],
    ),
    "abstain": (
        [f"{UNIT}::test_must_fail_abstain_is_never_packaged"],
        [
            "test_must_fail_abstain_is_never_packaged[abstain0]",
            "test_must_fail_abstain_is_never_packaged[abstain1]",
            "test_must_fail_abstain_is_never_packaged[abstain2]",
            "test_must_fail_abstain_is_never_packaged[abstain3]",
        ],
    ),
}


def _pytest(node_ids: list[str], guard_off: str | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    args = [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", *node_ids]
    if guard_off:
        env["C_LOOP_C_GUARD_OFF"] = guard_off
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "tests" / "dms"), env.get("PYTHONPATH", "")]
        )
        args[3:3] = ["-p", "c_loop_c_guard_off"]
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
