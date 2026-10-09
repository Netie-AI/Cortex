"""CLARIFY (#307) guard-removed proofs: each must-fail fails without its guard.

Each case reruns the named must-fail tests in a subprocess with one guard
switched off (tests/test_clarify/clarify_guard_off.py) and asserts they FAIL,
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
HERE = "tests/test_clarify"
CONTRACT = f"{HERE}/test_clarify_contract.py"
DETECT = f"{HERE}/test_clarify_detect.py"
HTTP = f"{HERE}/test_clarify_contract_ask.py"

NEVER_A_NUMBER = [
    f"test_must_fail_ambiguous_ask_never_returns_a_number_when_on[{q}]"
    for q in ("recent revenue", "What was revenue recently?", "capacity", "delayed", "high risk")
]

CASES: dict[str, tuple[list[str], list[str]]] = {
    "ambiguity_detect": (
        [
            f"{DETECT}::test_must_fail_known_ambiguous_ask_is_clarified",
            f"{HTTP}::test_must_fail_ambiguous_ask_never_returns_a_number_when_on",
        ],
        [
            "test_must_fail_known_ambiguous_ask_is_clarified[recent revenue]",
            "test_must_fail_known_ambiguous_ask_is_clarified[capacity]",
            "test_must_fail_known_ambiguous_ask_is_clarified[how much stock do we have]",
            *NEVER_A_NUMBER,
        ],
    ),
    "ask_gate": (
        [f"{HTTP}::test_must_fail_ambiguous_ask_never_returns_a_number_when_on"],
        NEVER_A_NUMBER,
    ),
    "clarify_shape": (
        [
            f"{CONTRACT}::test_must_fail_clarify_without_two_to_five_options_is_refused",
            f"{CONTRACT}::test_must_fail_clarify_with_duplicate_option_ids_is_refused",
            f"{CONTRACT}::test_must_fail_clarify_answer_never_carries_a_number",
            f"{DETECT}::test_must_fail_engine_refuses_clarify_without_options",
            f"{DETECT}::test_must_fail_engine_refuses_clarify_without_ambiguity_type",
            f"{DETECT}::test_must_fail_engine_refuses_option_without_interpretation",
        ],
        [
            "test_must_fail_clarify_without_two_to_five_options_is_refused[0]",
            "test_must_fail_clarify_without_two_to_five_options_is_refused[1]",
            "test_must_fail_clarify_without_two_to_five_options_is_refused[6]",
            "test_must_fail_clarify_with_duplicate_option_ids_is_refused",
            "test_must_fail_clarify_answer_never_carries_a_number[overrides0]",
            "test_must_fail_clarify_answer_never_carries_a_number[overrides1]",
            "test_must_fail_clarify_answer_never_carries_a_number[overrides4]",
            "test_must_fail_engine_refuses_clarify_without_options[0]",
            "test_must_fail_engine_refuses_clarify_without_options[1]",
            "test_must_fail_engine_refuses_clarify_without_ambiguity_type[None]",
            "test_must_fail_engine_refuses_clarify_without_ambiguity_type[metric]",
            "test_must_fail_engine_refuses_option_without_interpretation",
        ],
    ),
    "unambiguous_anchor": (
        [
            f"{DETECT}::test_must_fail_unambiguous_asks_are_never_clarified",
            f"{HTTP}::test_must_fail_unambiguous_ask_is_answered_not_clarified_when_on",
        ],
        [
            "test_must_fail_unambiguous_asks_are_never_clarified",
            *(
                f"test_must_fail_unambiguous_ask_is_answered_not_clarified_when_on[{q}]"
                for q in (
                    "what is our total revenue",
                    "Show warehouse capacity utilisation",
                    "how many delayed shipments",
                    "revenue in the last 7 days",
                )
            ),
        ],
    ),
}


def _pytest(node_ids: list[str], guard_off: str | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    args = [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", *node_ids]
    if guard_off:
        env["CLARIFY_GUARD_OFF"] = guard_off
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT / HERE), env.get("PYTHONPATH", "")])
        args[3:3] = ["-p", "clarify_guard_off"]
    return subprocess.run(
        args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=600, check=False
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
