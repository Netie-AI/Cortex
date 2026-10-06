"""#326: auto-merge only tier:fast with CLEAR + different-family AGREE on the exact head.

Every must-fail asserts the decision is not merge/queue. Guard-removed proofs for
each condition live in test_auto_merge_guard_mutations.py.
"""
from __future__ import annotations

import subprocess
from typing import Any

import pytest

import scripts.auto_merge_if_perfect as am

HEAD = "5a8cba6fe59f70cbb271c67fc88a98b54e56074a"
OLD = "c9cc8649478b80327b44ac0af8f6dfd27da2b2bc"
BODY = (
    "<!-- CURSOR_AGENT_PR_BODY_BEGIN -->\n"
    "tier:fast\n\n"
    "Refs #1. Docs only.\n\n"
    "Writer-Model: claude-opus-5.5\n"
    "<!-- CURSOR_AGENT_PR_BODY_END -->"
)


def _pr(**over: Any) -> dict[str, Any]:
    pr: dict[str, Any] = {
        "isDraft": False,
        "baseRefName": "main",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "headRefOid": HEAD,
        "body": BODY,
        "labels": [],
        "commits": [{"oid": HEAD, "messageBody": "Co-authored-by: Jian Hong <j@x>"}],
        "url": "https://github.com/Netie-AI/Cortex/pull/1",
    }
    pr.update(over)
    return pr


def _runs(sha: str = HEAD, **conclusions: str | None) -> list[dict[str, Any]]:
    """Check runs on `sha`; pass name=None to drop one, name='in_progress' to hold it."""
    out = []
    for i, name in enumerate(am.REQUIRED, start=100):
        conc = conclusions.get(name.replace("-", "_"), "success")
        if conc is None:
            continue
        status = "in_progress" if conc == "in_progress" else "completed"
        out.append(
            {
                "id": i,
                "name": name,
                "head_sha": sha,
                "status": status,
                "conclusion": None if status == "in_progress" else conc,
            }
        )
    return out


def _comment(cid: int, body: str, login: str = "jian-hong") -> dict[str, Any]:
    return {"id": cid, "user": {"login": login}, "body": body}


def _clear(sha: str = HEAD, cid: int = 10, login: str = "jian-hong") -> dict[str, Any]:
    return _comment(cid, f"## CLEAR @ exact head `{sha}`\n\nCI 5/5 on this exact SHA.", login)


def _agree(
    sha: str = HEAD, model: str = "grok-4.7", cid: int = 20, login: str = "cursor[bot]"
) -> dict[str, Any]:
    body = f"AGREE @ {sha}\n\nmodel={model} (different-family; writer claude-opus-5.5)"
    return _comment(cid, body, login)


def _decide(pr: dict[str, Any] | None = None, runs=None, comments=None) -> am.Decision:
    return am.decide(
        _pr() if pr is None else pr,
        _runs() if runs is None else runs,
        [_clear(), _agree()] if comments is None else comments,
    )


def _refused(d: am.Decision, reason: str | None = None) -> None:
    assert d.action in {"skip", "wait"}, d
    if reason is not None:
        assert reason in d.reason, d


# --- positive ----------------------------------------------------------------


def test_positive_tier_fast_clear_and_different_family_agree_on_exact_head_merges():
    d = _decide()
    assert d == am.Decision("merge", d.reason, HEAD)
    assert d.head_sha == HEAD


def test_positive_tier_fast_label_and_head_field_clear_merges():
    pr = _pr(body="Refs #1.\n\nWriter-Model: gpt-5.6-sol", labels=[{"name": "tier:fast"}])
    clear = _comment(
        10,
        "## CLEAR — exact-head tip-green 5/5\n\n**PR:** #1\n"
        f"**Head:** `{HEAD}` (exact)\n**Base / tip:** `{OLD}`",
    )
    agree = _comment(
        20,
        f"**AGREE-RELEASABLE** @ `{HEAD}`\n\nDifferent-family review (grok-4.7; writer gpt)",
        "cursor[bot]",
    )
    assert _decide(pr, comments=[clear, agree]).action == "merge"


def test_positive_writer_model_commit_trailer_sets_author_family():
    pr = _pr(
        body="tier:fast\n\nRefs #1.",
        commits=[{"oid": HEAD, "messageBody": "Fix.\n\nWriter-Model: gpt-5.6-sol"}],
    )
    assert _decide(pr).action == "merge"


def test_positive_blocked_state_queues():
    assert _decide(_pr(mergeStateStatus="BLOCKED")).action == "queue"


def test_positive_reclear_after_hold_counts():
    hold = _comment(30, f"## PR Bot — HOLD CLEAR @ `{HEAD[:8]}`")
    comments = [_clear(cid=10), _agree(cid=20), hold, _clear(cid=40), _agree(cid=50)]
    assert _decide(comments=comments).action == "merge"


# --- 1. tier -------------------------------------------------------------------


def test_must_fail_no_tier():
    _refused(_decide(_pr(body="Refs #1.\n\nWriter-Model: claude-opus-5.5")), "no tier:fast")


def test_must_fail_tier_full_body():
    body = BODY.replace("tier:fast", "tier:full")
    _refused(_decide(_pr(body=body)), "tier:full")


def test_must_fail_tier_full_label_overrides_tier_fast_body():
    _refused(_decide(_pr(labels=[{"name": "tier:full"}])), "tier:full")


def test_must_fail_tier_fast_only_below_first_lines():
    body = "Refs #1.\n\na\n\nb\n\nc\n\nd\n\ntier:fast\n\nWriter-Model: claude-opus-5.5"
    _refused(_decide(_pr(body=body)), "no tier:fast")


# --- 2. draft ------------------------------------------------------------------


def test_must_fail_draft():
    _refused(_decide(_pr(isDraft=True)), "draft")


# --- 3. required checks on the exact head ---------------------------------------


@pytest.mark.parametrize("name", am.REQUIRED)
def test_must_fail_required_check_missing(name: str):
    _refused(_decide(runs=_runs(**{name.replace("-", "_"): None})), f"{name}=missing")


@pytest.mark.parametrize("name", am.REQUIRED)
@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "stale"])
def test_must_fail_required_check_failing(name: str, conclusion: str):
    d = _decide(runs=_runs(**{name.replace("-", "_"): conclusion}))
    assert d.action == "skip", d


@pytest.mark.parametrize("name", am.REQUIRED)
def test_must_fail_required_check_in_progress_waits(name: str):
    d = _decide(runs=_runs(**{name.replace("-", "_"): "in_progress"}))
    assert d.action == "wait", d


def test_must_fail_required_checks_green_only_on_older_head():
    _refused(_decide(runs=_runs(OLD)), "lint-type-test=missing")


def test_must_fail_latest_rerun_failed_after_green():
    rerun = {
        "id": 999,
        "name": "rls-proof",
        "head_sha": HEAD,
        "status": "completed",
        "conclusion": "failure",
    }
    assert _decide(runs=[*_runs(), rerun]).action == "skip"


# --- 4. CLEAR on the exact head --------------------------------------------------


def test_must_fail_no_clear():
    _refused(_decide(comments=[_agree()]), "no CLEAR")


def test_must_fail_clear_on_older_head():
    _refused(_decide(comments=[_clear(OLD), _agree()]), "no CLEAR")


def test_must_fail_clear_short_sha():
    short = _comment(10, f"## PR Bot — CLEAR #1 @ `{HEAD[:8]}`")
    _refused(_decide(comments=[short, _agree()]), "no CLEAR")


def test_must_fail_clear_from_untrusted_login():
    _refused(_decide(comments=[_clear(login="mallory"), _agree()]), "no CLEAR")


def test_must_fail_clear_line_with_negation():
    hold = _comment(10, f"## CLEAR not yet — HOLD @ `{HEAD}`")
    _refused(_decide(comments=[hold, _agree()]), "no CLEAR")


def test_must_fail_head_field_conflicts_with_other_head():
    clear = _comment(10, f"## CLEAR — exact-head 5/5\n\n**Head:** `{HEAD}`\n**Head:** `{OLD}`")
    _refused(_decide(comments=[clear, _agree()]), "no CLEAR")


def test_must_fail_combined_lead_clear_agree():
    combined = _comment(10, f"**Lead CLEAR+AGREE** — head `{HEAD}` model=grok-4.7")
    _refused(_decide(comments=[combined]), "no CLEAR")


# --- 5. AGREE, different family, exact head ---------------------------------------


def test_must_fail_agree_missing():
    _refused(_decide(comments=[_clear()]), "no different-family")


def test_must_fail_agree_same_family():
    _refused(
        _decide(comments=[_clear(), _agree(model="claude-sonnet-5.5")]), "no different-family"
    )


def test_must_fail_agree_on_older_head():
    _refused(_decide(comments=[_clear(), _agree(OLD)]), "no different-family")


def test_must_fail_agree_without_model():
    no_model = _comment(20, f"AGREE @ {HEAD}\n\nDifferent-family review of head.", "cursor[bot]")
    _refused(_decide(comments=[_clear(), no_model]), "no different-family")


def test_must_fail_agree_unknown_model_family():
    _refused(_decide(comments=[_clear(), _agree(model="mystery-9")]), "no different-family")


def test_must_fail_agree_from_untrusted_login():
    _refused(_decide(comments=[_clear(), _agree(login="mallory")]), "no different-family")


def test_must_fail_author_family_unknown():
    _refused(_decide(_pr(body="tier:fast\n\nRefs #1.")), "author model family")


def test_must_fail_author_family_conflicting():
    pr = _pr(commits=[{"oid": HEAD, "messageBody": "x\n\nWriter-Model: grok-4.7"}])
    _refused(_decide(pr), "author model family")


# --- any head move voids 4 and 5 --------------------------------------------------


def test_must_fail_head_moved_after_agree():
    comments = [_clear(OLD, cid=10), _agree(OLD, cid=20), _clear(cid=30)]
    _refused(_decide(comments=comments), "no different-family")


def test_must_fail_head_moved_after_clear_and_agree():
    _refused(_decide(comments=[_clear(OLD), _agree(OLD)]), "no CLEAR")


def test_must_fail_void_after_agree():
    void = _comment(30, f"## VOID CLEAR+AGREE — tip moved\n\nPrior CLEAR @ `{HEAD[:8]}` VOID.")
    _refused(_decide(comments=[_clear(), _agree(), void]), "no CLEAR")


# --- 6. the merge is pinned to the decided head -----------------------------------


@pytest.mark.parametrize("mode", ["merge", "queue"])
def test_merge_cmd_pins_head_sha(mode: str):
    cmd = am.merge_cmd("7", mode, HEAD)
    assert cmd[cmd.index("--match-head-commit") + 1] == HEAD
    assert ("--auto" in cmd) is (mode == "queue")


def test_main_merges_at_decided_head_sha(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(am, "_snapshot", lambda n: (_pr(), _runs(), [_clear(), _agree()]))
    monkeypatch.setattr(subprocess, "check_call", lambda cmd: calls.append(cmd))
    assert am.main(["--apply", "--pr", "7"]) == 0
    assert len(calls) == 1
    assert calls[0][calls[0].index("--match-head-commit") + 1] == HEAD


def test_main_queue_fallback_stays_pinned(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []

    def check_call(cmd: list[str]) -> None:
        calls.append(cmd)
        if "--auto" not in cmd:
            raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(am, "_snapshot", lambda n: (_pr(), _runs(), [_clear(), _agree()]))
    monkeypatch.setattr(subprocess, "check_call", check_call)
    assert am.main(["--apply", "--pr", "7"]) == 0
    assert [c[c.index("--match-head-commit") + 1] for c in calls] == [HEAD, HEAD]


def test_main_never_merges_when_refused(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(am, "_snapshot", lambda n: (_pr(), _runs(), [_clear()]))
    monkeypatch.setattr(subprocess, "check_call", lambda cmd: calls.append(cmd))
    assert am.main(["--apply", "--pr", "7"]) == 0
    assert calls == []


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("claude-opus-5.5", "claude"),
        ("opus", "claude"),
        ("gpt-5.6-sol", "gpt"),
        ("grok-4.7", "grok"),
        ("cursor-grok-4.6-high", "grok"),
        ("gemini-3.8-flash", "gemini"),
        ("composer-2.5", "composer"),
        ("mystery-9", None),
        ("", None),
    ],
)
def test_model_family(model: str, family: str | None):
    assert am.model_family(model) == family
