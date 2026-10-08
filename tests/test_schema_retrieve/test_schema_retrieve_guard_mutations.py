"""SCHEMA-RETRIEVE (#306) guard-removed proofs: each must-fail fails without its guard.

Each case reruns the named must-fail tests in a subprocess with guards switched
off (tests/test_schema_retrieve/schema_retrieve_guard_off.py) and asserts every
one of them FAILS, then that the same tests pass with the guards on. A must-fail
that still passes without its guard is not testing the guard.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = "tests/test_schema_retrieve/test_schema_retrieve_core.py"
RERANK = "tests/test_schema_retrieve/test_schema_retrieve_freeroute.py"
HTTP = "tests/dms/test_schema_retrieve_contract_ask.py"

CASES: dict[str, list[str]] = {
    # must-fail 1: a table outside the grant is never returned
    "grant": [
        f"{CORE}::test_must_fail_table_outside_grant_never_returned",
        f"{CORE}::test_must_fail_table_outside_grant_never_a_join_bridge",
        f"{CORE}::test_must_fail_out_of_grant_memory_table_never_returned",
        f"{CORE}::test_must_fail_reranker_cannot_add_a_table_outside_the_grant",
        f"{HTTP}::test_must_fail_contract_ask_never_returns_a_table_outside_the_grant",
    ],
    "rerank_scope": [
        f"{CORE}::test_must_fail_reranker_cannot_add_a_table_outside_the_grant",
        f"{RERANK}::test_must_fail_freeroute_rerank_never_adds_an_ungranted_table",
    ],
    # must-fail 2: Space B never sees Space A's tables. The retriever's own
    # filter alone is what stops a leaky port; with the #290 store also
    # unguarded, the real store leaks too.
    "space": [
        f"{CORE}::test_must_fail_space_b_never_sees_space_a_tables_from_a_leaky_port",
    ],
    "space,cmem_space_isolation": [
        f"{CORE}::test_must_fail_space_b_never_sees_space_a_tables",
        f"{CORE}::test_must_fail_space_b_never_sees_space_a_tables_from_a_leaky_port",
        f"{HTTP}::test_must_fail_contract_ask_space_b_never_sees_space_a_tables",
    ],
    # must-fail 3: a scored-pack id never causes a memory write (#290 guard reused)
    "cmem_scored_pack": [
        f"{CORE}::test_must_fail_scored_pack_id_never_writes_memory",
        f"{CORE}::test_must_fail_scored_pack_catalog_source_never_writes_memory",
        f"{HTTP}::test_must_fail_contract_ask_scored_pack_never_writes_memory",
    ],
}


def _pytest(node_ids: list[str], guard_off: str | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    args = [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", *node_ids]
    if guard_off:
        env["SCHEMA_RETRIEVE_GUARD_OFF"] = guard_off
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "tests" / "test_schema_retrieve"), env.get("PYTHONPATH", "")]
        )
        args[3:3] = ["-p", "schema_retrieve_guard_off"]
    return subprocess.run(
        args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=300, check=False
    )


@pytest.mark.parametrize("guards", sorted(CASES))
def test_must_fail_tests_fail_without_their_guard(guards: str) -> None:
    node_ids = CASES[guards]
    off = _pytest(node_ids, guards)
    failed = [line for line in off.stdout.splitlines() if line.startswith("FAILED ")]
    assert off.returncode == 1, off.stdout + off.stderr
    for node in node_ids:
        assert any(node in line for line in failed), (
            f"{node} passed with {guards} off:\n{off.stdout}"
        )

    on = _pytest(node_ids, None)
    assert on.returncode == 0, on.stdout + on.stderr
