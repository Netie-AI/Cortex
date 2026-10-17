"""VERIFIED-QUERY (#309) guard-removed proofs: each must-fail fails without its guard.

Each case reruns the named must-fail tests in a subprocess with one guard
switched off (tests/test_memory/vq_guard_off.py) and asserts they FAIL, then
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
UNIT = "tests/test_memory/test_verified_query.py"
HTTP = "tests/dms/test_verified_query_contract_ask.py"

CASES: dict[str, list[str]] = {
    "confirm_gate": [
        f"{UNIT}::test_must_fail_unconfirmed_query_never_reused",
        f"{UNIT}::test_must_fail_unconfirmed_query_never_reused_after_non_steward_confirm",
        f"{UNIT}::test_must_fail_unconfirmed_query_never_reused_on_a_real_result_check",
        f"{UNIT}::test_must_fail_revoked_query_never_reused_after_plain_solution_confirm",
        f"{HTTP}::test_must_fail_contract_ask_unconfirmed_query_never_reused",
    ],
    "revoke": [
        f"{UNIT}::test_must_fail_revoked_query_never_reused",
        f"{UNIT}::test_must_fail_revoked_query_never_reused_after_plain_solution_confirm",
        f"{HTTP}::test_must_fail_contract_ask_revoked_query_never_reused",
    ],
    "scored_pack": [
        f"{UNIT}::test_must_fail_scored_pack_never_writes_or_reads_verified_queries",
        f"{HTTP}::test_must_fail_contract_ask_scored_pack_never_reads_verified_queries",
    ],
    "space_isolation": [
        f"{UNIT}::test_must_fail_space_b_never_sees_space_a_verified_query",
        f"{HTTP}::test_must_fail_contract_ask_space_b_never_reuses_space_a_query",
    ],
    "example_not_executed": [
        f"{UNIT}::test_must_fail_stored_example_is_not_the_generated_sql",
    ],
    "grant_gate": [f"{HTTP}::test_must_fail_generated_sql_never_widens_beyond_the_grant"],
    # Serve-time check: a near-miss example is never the answer.
    "example_echo": [
        f"{HTTP}::test_must_fail_near_miss_example_never_served",
        f"{HTTP}::test_must_fail_near_miss_listing_example_never_served",
        f"{HTTP}::test_must_fail_copied_example_for_another_field_never_served",
        f"{HTTP}::test_must_fail_copied_example_ignores_a_number_never_served",
        f"{HTTP}::test_must_fail_dropped_filter_example_never_served",
        # Review @ 2161c14: a copy is never served for another question.
        f"{HTTP}::test_must_fail_copy_with_added_qualifier_never_served",
        f"{HTTP}::test_must_fail_reordered_words_are_not_the_confirmed_question",
        f"{HTTP}::test_must_fail_copy_for_the_opposite_order_never_served",
        f"{HTTP}::test_must_fail_copy_for_a_negated_question_never_served",
        f"{HTTP}::test_must_fail_renamed_copy_is_still_a_copy",
        f"{HTTP}::test_must_fail_paraphrase_reaches_certified_example_not_the_phrase_table",
    ],
    # Only examples inside the grant and the current schema reach the model.
    "example_scope": [f"{HTTP}::test_must_fail_example_outside_grant_or_schema_never_in_prompt"],
}


def _pytest(node_ids: list[str], guard_off: str | None) -> subprocess.CompletedProcess[str]:
    from CortexOS.execution.warehouse import close_cached_connections

    # An earlier HTTP test in this process can leave a cached read-only DuckDB
    # instance open on the warehouse. The child's fixture opens it read-write,
    # so that shared lock would make every child test ERROR (not FAIL) and the
    # proof below would read a lock error as a surviving must-fail.
    close_cached_connections()
    env = dict(os.environ)
    args = [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", *node_ids]
    if guard_off:
        env["VQ_GUARD_OFF"] = guard_off
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "tests" / "test_memory"), env.get("PYTHONPATH", "")]
        )
        args[3:3] = ["-p", "vq_guard_off"]
    return subprocess.run(
        args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=600, check=False
    )


def _outcomes(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        word, _, rest = line.partition(" ")
        if word in {"PASSED", "FAILED", "ERROR"} and "::" in rest:
            out[rest.split(" - ")[0].strip()] = word
    return out


@pytest.mark.parametrize("guard", sorted(CASES))
def test_must_fail_tests_fail_without_their_guard(guard: str) -> None:
    node_ids = CASES[guard]
    off = _pytest(node_ids, guard)
    outcomes = _outcomes(off.stdout)
    assert off.returncode == 1, off.stdout + off.stderr
    for node in node_ids:
        ran = {k: v for k, v in outcomes.items() if k == node or k.startswith(node + "[")}
        assert ran, f"{node} did not run with guard {guard} off:\n{off.stdout}"
        survivors = [k for k, v in ran.items() if v != "FAILED"]
        assert not survivors, f"passed with guard {guard} off: {survivors}\n{off.stdout}"

    on = _pytest(node_ids, None)
    assert on.returncode == 0, on.stdout + on.stderr
