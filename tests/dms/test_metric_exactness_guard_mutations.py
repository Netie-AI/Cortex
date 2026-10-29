"""#412 guard-removed proof: the must-fails fail when the metric check is off.

The subprocess loads tests/dms/metric_exactness_guard_off.py, which serves the
chosen metric without the meaning check. Each must-fail must then FAIL. The
same tests pass with the check on.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTTP = "tests/dms/test_metric_exactness_412.py"
# #424: the real OpenVault adapter through a scripted FreeRoute.
ADAPTER = "tests/dms/test_metric_choice_adapter_424.py"

MUST_FAIL = [
    "test_must_fail_near_miss_is_not_served_as_delayed_count",
    "test_must_fail_near_miss_reaches_the_model_step",
    "test_must_fail_non_boolean_check_is_not_served",
    "test_must_fail_served_answer_carries_no_refusal_prose",
]


def _pytest(guard_off: bool) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    args = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-rf",
        "-p",
        "no:cacheprovider",
        HTTP,
        ADAPTER,
        "-k",
        "must_fail",
    ]
    if guard_off:
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "tests" / "dms"), env.get("PYTHONPATH", "")]
        )
        args[5:5] = ["-p", "metric_exactness_guard_off"]
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )


def test_must_fail_tests_fail_without_the_exactness_guard() -> None:
    off = _pytest(True)
    failed = [line for line in off.stdout.splitlines() if line.startswith("FAILED ")]
    assert off.returncode != 0, off.stdout + off.stderr
    for name in MUST_FAIL:
        assert any(name in line for line in failed), (
            f"{name} passed with the exactness guard off:\n{off.stdout}\n{off.stderr}"
        )

    on = _pytest(False)
    assert on.returncode == 0, on.stdout + on.stderr
