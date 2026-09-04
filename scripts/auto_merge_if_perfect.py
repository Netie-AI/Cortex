"""Verdict helper: squash-merge a PR only when every required check is green.

Cloud-agent `gh` is read-only. CI job `auto-merge` is what actually merges.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

REQUIRED = (
    "lint-type-test",
    "base-install",
    "protected-paths",
    "rls-proof",
    "secrets-scan",
)
IGNORE_NAMES = frozenset({"auto-merge"})
IGNORE_CONCLUSIONS = frozenset({"CANCELLED", "SKIPPED", "NEUTRAL"})
ALLOWED_BASES = frozenset({"main"})


_ALIASES = {
    "PASS": "SUCCESS",
    "FAIL": "FAILURE",
    "PENDING": "IN_PROGRESS",
    "SKIPPING": "SKIPPED",
}


def _norm_name(check: dict[str, Any]) -> str:
    return str(check.get("name") or "").strip()


def _conclusion(check: dict[str, Any]) -> str:
    raw = check.get("conclusion") or check.get("state") or check.get("bucket") or ""
    key = str(raw).upper().replace(" ", "_")
    return _ALIASES.get(key, key)


def as_rollup_checks(raw: Any) -> list[dict[str, Any]]:
    """Normalize `gh pr checks --json` or statusCheckRollup into verdict checks."""
    items = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    for check in items:
        if not isinstance(check, dict):
            continue
        conc = _conclusion(check)
        bucket = str(check.get("bucket") or "").lower()
        status = str(check.get("status") or "").upper()
        if bucket == "pending" or conc in {"", "NONE", "IN_PROGRESS", "PENDING", "QUEUED"}:
            status = status or "IN_PROGRESS"
        elif not status:
            status = "COMPLETED"
        out.append({"name": _norm_name(check), "conclusion": conc, "status": status})
    return out


def verdict(pr: dict[str, Any]) -> str:
    """Return merge | wait | skip | queue."""
    if pr.get("isDraft"):
        return "skip"
    if str(pr.get("baseRefName") or "") not in ALLOWED_BASES:
        return "skip"
    latest: dict[str, dict[str, Any]] = {}
    for check in pr.get("statusCheckRollup") or []:
        if not isinstance(check, dict):
            continue
        name = _norm_name(check)
        if not name or name in IGNORE_NAMES:
            continue
        if _conclusion(check) in IGNORE_CONCLUSIONS:
            continue
        latest[name] = check

    waiting = False
    for check in latest.values():
        conc = _conclusion(check)
        status = str(check.get("status") or "").upper()
        in_flight = status in {"QUEUED", "IN_PROGRESS", "PENDING"} or conc in {"", "NONE"}
        if conc in {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
            return "skip"
        if conc == "SUCCESS":
            continue
        if in_flight:
            waiting = True
            continue
        return "skip"

    for name in REQUIRED:
        check = latest.get(name)
        if check is None or _conclusion(check) != "SUCCESS":
            waiting = True

    if waiting:
        return "wait"
    if pr.get("mergeable") != "MERGEABLE":
        return "wait"
    state = str(pr.get("mergeStateStatus") or "").upper()
    if state in {"DIRTY", "BEHIND"}:
        return "skip"
    if state == "BLOCKED":
        return "queue"
    return "merge"


def _gh(args: list[str]) -> Any:
    raw = subprocess.check_output(["gh", *args], text=True)
    return json.loads(raw)


def _gh_pr_view(number: str) -> dict[str, Any]:
    # Do not request statusCheckRollup — GITHUB_TOKEN cannot read nested workflowRun.
    data = _gh(
        [
            "pr",
            "view",
            number,
            "--json",
            "isDraft,mergeable,mergeStateStatus,baseRefName,url,title",
        ]
    )
    if not isinstance(data, dict):
        raise OSError("gh pr view did not return an object")
    checks = _gh(["pr", "checks", number, "--json", "name,state,bucket"])
    data["statusCheckRollup"] = as_rollup_checks(checks)
    return data


def _merge(number: str, mode: str) -> None:
    cmd = ["gh", "pr", "merge", number, "--squash", "--delete-branch"]
    if mode == "queue":
        cmd.append("--auto")
    subprocess.check_call(cmd)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    apply = "--apply" in args
    number = os.environ.get("PR_NUMBER", "").strip()
    if "--pr" in args:
        idx = args.index("--pr")
        number = args[idx + 1] if idx + 1 < len(args) else number
    if not number:
        print("PR_NUMBER or --pr is required", file=sys.stderr)
        return 2
    deadline = time.time() + 10 * 60
    try:
        while True:
            pr = _gh_pr_view(number)
            decision = verdict(pr)
            print(
                f"{decision} {pr.get('url')} mergeable={pr.get('mergeable')} "
                f"state={pr.get('mergeStateStatus')}"
            )
            if decision == "skip":
                return 0
            if decision in {"merge", "queue"}:
                if not apply:
                    return 0
                try:
                    _merge(number, decision)
                except subprocess.CalledProcessError:
                    if decision == "merge":
                        print("direct merge failed; enabling GitHub auto-merge")
                        try:
                            _merge(number, "queue")
                        except subprocess.CalledProcessError:
                            print("could not merge (token or protection); leaving PR open")
                            return 0
                    else:
                        print("could not enable auto-merge; leaving PR open")
                        return 0
                return 0
            if time.time() >= deadline:
                print("waited for sibling checks; still not perfect — leaving PR open")
                return 0
            time.sleep(15)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        print(f"auto-merge probe failed ({exc}); leaving PR open")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
