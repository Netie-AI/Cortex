"""Verdict helper: squash-merge a PR only on the fast lane with exact-head review.

Cloud-agent `gh` is read-only. CI job `auto-merge` is what actually merges, and it
merges only when `decide()` returns merge/queue. Every condition below must hold
on the PR's exact head SHA; any head move voids the CLEAR and the AGREE.

1. Tier. `tier:fast` is a label or appears in the first five non-empty body lines
   (HTML comments ignored). `tier:full` in any label or anywhere in the body, or
   no tier, means never.
2. Not a draft, base `main`.
3. Every REQUIRED check has a latest check run on the head SHA that concluded
   success, and no other check on that SHA failed (`verdict()`).
4. CLEAR. A comment from a CLEAR_AUTHORS login whose first line, after markdown
   `#`, `>`, `*` and backticks are stripped, starts with `CLEAR` or `PR Bot CLEAR`
   (optionally `PR Bot -/:/em-dash CLEAR`) and that names the head. It names the
   head when the first line contains the full 40-hex head SHA, or when the first
   line has no full SHA and every `Head: <sha>` field line carries exactly the
   head SHA. Accepted: "## CLEAR @ exact head `<sha>`", "**PR Bot CLEAR, ... at
   exact head `<sha>`**", "## CLEAR — exact-head tip-green 5/5" followed by
   "**Head:** `<sha>` (exact)". Short SHAs, `CLEAR+AGREE`, `CLEAR-...`, and first
   lines carrying not/no/void/hold/revoke(d)/withdrawn/stale/blocked/disagree do
   not count.
5. AGREE. A comment from an AGREE_AUTHORS login whose stripped first line starts
   with `AGREE` or `AGREE-RELEASABLE`, has none of those words, and names the
   head as in 4. Its body must declare the reviewer model in exactly one family
   via `model=<id>`, `Reviewer-Model: <id>`, or `Different-family review (<id>`.
   The author family comes from `Writer-Model: <id>` at the start of a PR body
   line or as a commit trailer on any PR commit. Unknown, missing, or conflicting
   author families refuse, and so does a reviewer in the author's family.
6. The merge passes `--match-head-commit <head SHA>`.

A trusted comment whose first line carries VOID/HOLD/REVOKE(D)/WITHDRAWN/DISAGREE/
BLOCKED and whose body mentions the head SHA (full, or any 7+ hex prefix) voids
every CLEAR and AGREE posted before it (by comment id). Only issue comments on the PR are read.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
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

# The repo is public, so anyone can comment. Only these logins can CLEAR or AGREE.
CLEAR_AUTHORS = frozenset({"jian-hong"})
AGREE_AUTHORS = frozenset({"jian-hong", "cursor[bot]"})

TIER_LINES = 5
_FAMILY = {
    "claude": "claude",
    "opus": "claude",
    "sonnet": "claude",
    "haiku": "claude",
    "gpt": "gpt",
    "grok": "grok",
    "gemini": "gemini",
    "composer": "composer",
    "muse": "muse",
}

_SHA = re.compile(r"[0-9a-f]{40}")
_SHA_IN_TEXT = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{40}(?![0-9a-fA-F])")
_HEX_IN_TEXT = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{7,40}(?![0-9a-fA-F])")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HEAD_FIELD = re.compile(r"^[\s>_-]*Head\s*:\s*([0-9a-f]{40})(?![0-9a-fA-F])")
_TIER_FAST = re.compile(r"(?<![\w:-])tier:fast(?![\w-])")
_TIER_FULL = re.compile(r"(?<![\w:-])tier:full(?![\w-])", re.I)
_CLEAR_LINE = re.compile(r"^(?:PR Bot\s*[-:\u2013\u2014]?\s*)?CLEAR(?![\w+-])")
_AGREE_LINE = re.compile(r"^AGREE(?:-RELEASABLE)?(?![\w+-])")
_NEGATION = re.compile(
    r"\b(?:not|no|void|hold|revoked?|withdrawn|stale|blocked|disagree)\b", re.I
)
_VETO = re.compile(r"\b(?:void|hold|revoked?|withdrawn|disagree|blocked)\b", re.I)
_MODEL_ID = r"`?([A-Za-z0-9][\w.-]*)"
_REVIEWER_MODEL = (
    re.compile(r"(?<![\w-])model\s*=\s*" + _MODEL_ID),
    re.compile(r"(?im)^[\s>*_-]*Reviewer-Model\**\s*:\s*\**\s*" + _MODEL_ID),
    re.compile(r"(?i)Different-family review\s*\(\s*" + _MODEL_ID),
)
_WRITER_MODEL = re.compile(r"(?im)^[\s>*_-]*Writer-Model\**\s*:\s*\**\s*" + _MODEL_ID)


_ALIASES = {
    "PASS": "SUCCESS",
    "FAIL": "FAILURE",
    "PENDING": "IN_PROGRESS",
    "SKIPPING": "SKIPPED",
}


@dataclass(frozen=True)
class Decision:
    action: str  # merge | queue | wait | skip
    reason: str
    head_sha: str


def _norm_name(check: dict[str, Any]) -> str:
    return str(check.get("name") or "").strip()


def _conclusion(check: dict[str, Any]) -> str:
    raw = check.get("conclusion") or check.get("state") or check.get("bucket") or ""
    key = str(raw).upper().replace(" ", "_")
    return _ALIASES.get(key, key)


def as_rollup_checks(raw: Any) -> list[dict[str, Any]]:
    """Normalize `gh pr checks --json`, statusCheckRollup or check runs into verdict checks."""
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
    """Checks-and-mergeability sub-verdict: merge | wait | skip | queue.

    Never sufficient to merge on its own; `decide()` adds the tier, review and
    exact-head conditions and feeds this only check runs on the head SHA.
    """
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


def model_family(model: str) -> str | None:
    parts = [p for p in re.split(r"[-_./\s]+", model.strip().lower()) if p]
    if parts and parts[0] == "cursor":
        parts = parts[1:]
    return _FAMILY.get(parts[0]) if parts else None


def _one_family(models: list[str]) -> str | None:
    families = {model_family(m) for m in models}
    if len(families) != 1 or None in families:
        return None
    return families.pop()


def _strip_md(line: str) -> str:
    return line.replace("*", "").replace("`", "").strip().lstrip("#>").strip()


def _first_line(body: str) -> str:
    for line in _HTML_COMMENT.sub("", body).splitlines():
        if _strip_md(line):
            return _strip_md(line)
    return ""


def _shas(text: str) -> set[str]:
    return set(_SHA_IN_TEXT.findall(text))


def _names_head(body: str, head: str) -> bool:
    first = _shas(_first_line(body))
    if first:
        return head in first
    fields = [
        sha
        for line in _HTML_COMMENT.sub("", body).splitlines()
        for sha in _HEAD_FIELD.findall(_strip_md(line))
    ]
    return set(fields) == {head}


def _login(comment: dict[str, Any]) -> str:
    user = comment.get("user")
    return str(user.get("login") or "") if isinstance(user, dict) else ""


def _id(comment: dict[str, Any]) -> int:
    try:
        return int(comment.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _live_comments(comments: list[dict[str, Any]], head: str) -> list[dict[str, Any]]:
    """Trusted comments posted after the last veto that names `head`."""
    trusted = CLEAR_AUTHORS | AGREE_AUTHORS
    ordered = sorted(
        (c for c in comments if isinstance(c, dict) and _login(c) in trusted),
        key=_id,
    )
    after = 0
    for comment in ordered:
        body = str(comment.get("body") or "")
        mentions = any(head.startswith(tok) for tok in _HEX_IN_TEXT.findall(body))
        if _VETO.search(_first_line(body)) and mentions:
            after = _id(comment)
    return [c for c in ordered if _id(c) > after]


def _tier_refusal(pr: dict[str, Any]) -> str | None:
    labels = {
        str(label.get("name") or "") if isinstance(label, dict) else str(label)
        for label in pr.get("labels") or []
    }
    body = _HTML_COMMENT.sub("", str(pr.get("body") or ""))
    if any(_TIER_FULL.fullmatch(name) for name in labels) or _TIER_FULL.search(body):
        return "tier:full never auto-merges"
    head_lines = [line for line in body.splitlines() if line.strip()][:TIER_LINES]
    if "tier:fast" in labels or any(_TIER_FAST.search(line) for line in head_lines):
        return None
    return "no tier:fast label or body tag"


def _draft_refusal(pr: dict[str, Any]) -> str | None:
    if pr.get("isDraft"):
        return "draft"
    return None


def _clear_refusal(comments: list[dict[str, Any]], head: str) -> str | None:
    for comment in comments:
        if _login(comment) not in CLEAR_AUTHORS:
            continue
        body = str(comment.get("body") or "")
        line = _first_line(body)
        if not _CLEAR_LINE.match(line) or _NEGATION.search(line):
            continue
        if _names_head(body, head):
            return None
    return f"no CLEAR naming head {head}"


def author_family(pr: dict[str, Any]) -> str | None:
    models = _WRITER_MODEL.findall(str(pr.get("body") or ""))
    for commit in pr.get("commits") or []:
        if isinstance(commit, dict):
            models += _WRITER_MODEL.findall(str(commit.get("messageBody") or ""))
    return _one_family(models)


def _agree_refusal(
    pr: dict[str, Any], comments: list[dict[str, Any]], head: str
) -> str | None:
    author = author_family(pr)
    if author is None:
        return "author model family unknown or ambiguous (need Writer-Model:)"
    for comment in comments:
        if _login(comment) not in AGREE_AUTHORS:
            continue
        body = str(comment.get("body") or "")
        line = _first_line(body)
        if not _AGREE_LINE.match(line) or _NEGATION.search(line):
            continue
        if not _names_head(body, head):
            continue
        reviewer = _one_family([m for p in _REVIEWER_MODEL for m in p.findall(body)])
        if reviewer is None:
            continue
        if reviewer == author:
            continue
        return None
    return f"no different-family ({author}) AGREE naming head {head}"


def _checks_on_head(check_runs: list[dict[str, Any]], head: str) -> list[dict[str, Any]]:
    runs = [
        run
        for run in check_runs
        if isinstance(run, dict) and str(run.get("head_sha") or "") == head
    ]
    runs.sort(key=_id)
    return as_rollup_checks(runs)


def decide(
    pr: dict[str, Any],
    check_runs: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> Decision:
    """Pure merge decision. Only merge/queue may merge, and only at `head_sha`."""
    head = str(pr.get("headRefOid") or "")
    if not _SHA.fullmatch(head):
        return Decision("skip", "no head SHA", head)
    live = _live_comments(comments, head)
    refusal = (
        _tier_refusal(pr)
        or _draft_refusal(pr)
        or _clear_refusal(live, head)
        or _agree_refusal(pr, live, head)
    )
    if refusal:
        return Decision("skip", refusal, head)
    on_head = _checks_on_head(check_runs, head)
    action = verdict({**pr, "statusCheckRollup": on_head})
    seen = {c["name"]: c["conclusion"] or c["status"] for c in on_head}
    state = ", ".join(f"{name}={seen.get(name, 'missing')}" for name in REQUIRED)
    return Decision(action, f"checks on {head[:12]}: {state}", head)


def merge_cmd(number: str, mode: str, head_sha: str) -> list[str]:
    cmd = ["gh", "pr", "merge", number, "--squash", "--delete-branch"]
    cmd += ["--match-head-commit", head_sha]
    if mode == "queue":
        cmd.append("--auto")
    return cmd


def _gh(args: list[str]) -> Any:
    raw = subprocess.check_output(["gh", *args], text=True)
    return json.loads(raw)


def _gh_pages(path: str, key: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, 21):
        data = _gh(["api", f"{path}?per_page=100&page={page}"])
        items = data.get(key) if key and isinstance(data, dict) else data
        if not isinstance(items, list):
            raise OSError(f"gh api {path} did not return a list")
        out += items
        if len(items) < 100:
            return out
    raise OSError(f"gh api {path}: too many pages")


def _snapshot(number: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    # Do not request statusCheckRollup — GITHUB_TOKEN cannot read nested workflowRun.
    pr = _gh(
        [
            "pr",
            "view",
            number,
            "--json",
            "isDraft,mergeable,mergeStateStatus,baseRefName,url,title,"
            "headRefOid,body,labels,commits",
        ]
    )
    if not isinstance(pr, dict):
        raise OSError("gh pr view did not return an object")
    head = str(pr.get("headRefOid") or "")
    if not _SHA.fullmatch(head):
        raise OSError(f"gh pr view returned no head SHA ({head!r})")
    runs = _gh_pages(f"repos/{{owner}}/{{repo}}/commits/{head}/check-runs", "check_runs")
    comments = _gh_pages(f"repos/{{owner}}/{{repo}}/issues/{number}/comments")
    return pr, runs, comments


def _merge(number: str, mode: str, head_sha: str) -> None:
    subprocess.check_call(merge_cmd(number, mode, head_sha))


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
            pr, runs, comments = _snapshot(number)
            decision = decide(pr, runs, comments)
            print(
                f"{decision.action} {pr.get('url')} head={decision.head_sha} "
                f"mergeable={pr.get('mergeable')} state={pr.get('mergeStateStatus')} "
                f"reason={decision.reason}"
            )
            if decision.action == "skip":
                return 0
            if decision.action in {"merge", "queue"}:
                if not apply:
                    return 0
                try:
                    _merge(number, decision.action, decision.head_sha)
                except subprocess.CalledProcessError:
                    if decision.action == "merge":
                        print("direct merge failed; enabling GitHub auto-merge")
                        try:
                            _merge(number, "queue", decision.head_sha)
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
