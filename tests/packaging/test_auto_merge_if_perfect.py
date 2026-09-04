"""Auto-merge only when every required check is green."""
from __future__ import annotations

from scripts.auto_merge_if_perfect import as_rollup_checks, verdict


def test_draft_is_skip():
    assert verdict({"isDraft": True, "baseRefName": "main"}) == "skip"


def test_dms_v2_base_is_skip():
    assert verdict({"isDraft": False, "baseRefName": "dms-v2"}) == "skip"


def test_green_mergeable_is_merge():
    checks = [
        {"name": n, "conclusion": "SUCCESS", "status": "COMPLETED"}
        for n in (
            "lint-type-test",
            "base-install",
            "protected-paths",
            "rls-proof",
            "secrets-scan",
        )
    ]
    assert (
        verdict(
            {
                "isDraft": False,
                "baseRefName": "main",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": checks,
            }
        )
        == "merge"
    )


def test_failure_is_skip():
    checks = [
        {"name": "lint-type-test", "conclusion": "FAILURE", "status": "COMPLETED"},
        {"name": "base-install", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "protected-paths", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "rls-proof", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "secrets-scan", "conclusion": "SUCCESS", "status": "COMPLETED"},
    ]
    assert (
        verdict(
            {
                "isDraft": False,
                "baseRefName": "main",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "UNSTABLE",
                "statusCheckRollup": checks,
            }
        )
        == "skip"
    )


def test_pending_rls_is_wait():
    checks = [
        {"name": "lint-type-test", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "base-install", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "protected-paths", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "rls-proof", "conclusion": "", "status": "IN_PROGRESS"},
        {"name": "secrets-scan", "conclusion": "SUCCESS", "status": "COMPLETED"},
    ]
    assert (
        verdict(
            {
                "isDraft": False,
                "baseRefName": "main",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "UNSTABLE",
                "statusCheckRollup": checks,
            }
        )
        == "wait"
    )


def test_blocked_queues_auto_merge():
    checks = [
        {"name": n, "conclusion": "SUCCESS", "status": "COMPLETED"}
        for n in (
            "lint-type-test",
            "base-install",
            "protected-paths",
            "rls-proof",
            "secrets-scan",
        )
    ]
    assert (
        verdict(
            {
                "isDraft": False,
                "baseRefName": "main",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "BLOCKED",
                "statusCheckRollup": checks,
            }
        )
        == "queue"
    )


def test_cancelled_previous_run_ignored():
    checks = [
        {"name": "lint-type-test", "conclusion": "CANCELLED", "status": "COMPLETED"},
        {"name": "lint-type-test", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "base-install", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "protected-paths", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "rls-proof", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "secrets-scan", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "auto-merge", "status": "IN_PROGRESS"},
    ]
    assert (
        verdict(
            {
                "isDraft": False,
                "baseRefName": "main",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": checks,
            }
        )
        == "merge"
    )


def test_gh_pr_checks_json_maps_to_success():
    mapped = as_rollup_checks(
        [
            {"name": "lint-type-test", "state": "SUCCESS", "bucket": "pass"},
            {"name": "base-install", "bucket": "pass"},
            {"name": "protected-paths", "state": "pass"},
            {"name": "rls-proof", "bucket": "pass"},
            {"name": "secrets-scan", "state": "SUCCESS", "bucket": "pass"},
            {"name": "auto-merge", "bucket": "pending"},
        ]
    )
    assert (
        verdict(
            {
                "isDraft": False,
                "baseRefName": "main",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": mapped,
            }
        )
        == "merge"
    )
