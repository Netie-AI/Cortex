"""#326 guard-removed proofs: each must-fail fails with its guard reverted.

Each case reruns the named tests in a subprocess with one guard reverted in the
script source (tests/packaging/auto_merge_guard_off.py) and asserts every one of
them FAILS, then that all of them pass with the guards in place. A must-fail that
still passes without its guard is not testing the guard.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.packaging.auto_merge_guard_off import MUTATIONS

ROOT = Path(__file__).resolve().parents[2]
GATES = "tests/packaging/test_auto_merge_gates.py"
OLD = "tests/packaging/test_auto_merge_if_perfect.py"

CASES: dict[str, list[str]] = {
    "tier": [
        f"{GATES}::test_must_fail_no_tier",
        f"{GATES}::test_must_fail_tier_full_body",
    ],
    "tier_full": [f"{GATES}::test_must_fail_tier_full_label_overrides_tier_fast_body"],
    "tier_first_lines": [f"{GATES}::test_must_fail_tier_fast_only_below_first_lines"],
    "draft": [
        f"{GATES}::test_must_fail_draft",
        f"{OLD}::test_draft_is_skip",
    ],
    "required_checks": [
        *(
            f"{GATES}::test_must_fail_required_check_missing[{name}]"
            for name in (
                "lint-type-test",
                "base-install",
                "protected-paths",
                "rls-proof",
                "secrets-scan",
            )
        ),
        *(
            f"{GATES}::test_must_fail_required_check_failing[{conc}-{name}]"
            for conc in ("failure", "timed_out", "stale")
            for name in (
                "lint-type-test",
                "base-install",
                "protected-paths",
                "rls-proof",
                "secrets-scan",
            )
        ),
        f"{GATES}::test_must_fail_latest_rerun_failed_after_green",
    ],
    "checks_exact_head": [f"{GATES}::test_must_fail_required_checks_green_only_on_older_head"],
    "clear": [
        f"{GATES}::test_must_fail_no_clear",
        f"{GATES}::test_must_fail_clear_on_older_head",
        f"{GATES}::test_must_fail_head_moved_after_clear_and_agree",
    ],
    "clear_exact_head": [
        f"{GATES}::test_must_fail_clear_on_older_head",
        f"{GATES}::test_must_fail_clear_short_sha",
        f"{GATES}::test_must_fail_head_field_conflicts_with_other_head",
    ],
    "clear_authors": [f"{GATES}::test_must_fail_clear_from_untrusted_login"],
    "agree": [
        f"{GATES}::test_must_fail_agree_missing",
        f"{GATES}::test_must_fail_agree_same_family",
        f"{GATES}::test_must_fail_agree_on_older_head",
        f"{GATES}::test_must_fail_head_moved_after_agree",
        f"{GATES}::test_main_never_merges_when_refused",
    ],
    "agree_exact_head": [
        f"{GATES}::test_must_fail_agree_on_older_head",
        f"{GATES}::test_must_fail_head_moved_after_agree",
    ],
    "agree_family": [f"{GATES}::test_must_fail_agree_same_family"],
    "agree_model_declared": [f"{GATES}::test_must_fail_agree_without_model"],
    "author_family_known": [
        f"{GATES}::test_must_fail_author_family_unknown",
        f"{GATES}::test_must_fail_author_family_conflicting",
    ],
    "agree_authors": [f"{GATES}::test_must_fail_agree_from_untrusted_login"],
    "veto": [f"{GATES}::test_must_fail_void_after_agree"],
    "match_head_commit": [
        f"{GATES}::test_merge_cmd_pins_head_sha[merge]",
        f"{GATES}::test_merge_cmd_pins_head_sha[queue]",
        f"{GATES}::test_main_merges_at_decided_head_sha",
        f"{GATES}::test_main_queue_fallback_stays_pinned",
    ],
}


def _pytest(node_ids: list[str], guard_off: str | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    args = [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", *node_ids]
    if guard_off:
        env["AUTO_MERGE_GUARD_OFF"] = guard_off
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "tests" / "packaging"), env.get("PYTHONPATH", "")]
        )
        args[3:3] = ["-p", "auto_merge_guard_off"]
    return subprocess.run(
        args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=300, check=False
    )


def test_every_guard_has_a_proof():
    assert set(CASES) == set(MUTATIONS)


@pytest.mark.parametrize("guard", sorted(CASES))
def test_must_fail_tests_fail_without_their_guard(guard: str) -> None:
    off = _pytest(CASES[guard], guard)
    failed = [line for line in off.stdout.splitlines() if line.startswith("FAILED ")]
    assert off.returncode == 1, off.stdout + off.stderr
    for node in CASES[guard]:
        assert any(line.startswith(f"FAILED {node}") for line in failed), (
            f"{node} passed with guard {guard} reverted:\n{off.stdout}"
        )


def test_must_fail_tests_pass_with_guards_on() -> None:
    nodes = sorted({node for nodes in CASES.values() for node in nodes})
    on = _pytest(nodes, None)
    assert on.returncode == 0, on.stdout + on.stderr
