"""#326: the merge-flow comment templates in AGENTS.md parse as valid in decide().

The blocks are read from AGENTS.md itself, so the documented shapes and the code
cannot drift apart without this failing.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import scripts.auto_merge_if_perfect as am

ROOT = Path(__file__).resolve().parents[2]
HEAD = "5a8cba6fe59f70cbb271c67fc88a98b54e56074a"
OLD = "c9cc8649478b80327b44ac0af8f6dfd27da2b2bc"
WRITER = "claude-opus-5-5"
REVIEWER = "grok-4.7"

_BLOCK = re.compile(r"^```text (pr-body|clear|agree|void)\n(.*?)^```$", re.M | re.S)


def _templates() -> dict[str, str]:
    found: dict[str, str] = {}
    for kind, text in _BLOCK.findall((ROOT / "AGENTS.md").read_text(encoding="utf-8")):
        assert kind not in found, f"AGENTS.md has two {kind} templates"
        found[kind] = text
    assert set(found) == {"pr-body", "clear", "agree", "void"}, found
    return found


def _fill(text: str, head: str = HEAD, reviewer: str = REVIEWER) -> str:
    out = (
        text.replace("<full 40-char head sha>", head)
        .replace("<writer model id>", WRITER)
        .replace("<reviewer model id>", reviewer)
        .replace("<issue>", "326")
    )
    assert "<" not in out, f"unfilled placeholder in template:\n{out}"
    return out


def _runs(sha: str = HEAD) -> list[dict[str, object]]:
    return [
        {"id": i, "name": n, "head_sha": sha, "status": "completed", "conclusion": "success"}
        for i, n in enumerate(am.REQUIRED, start=100)
    ]


def _pr(body: str) -> dict[str, object]:
    return {
        "isDraft": False,
        "baseRefName": "main",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "headRefOid": HEAD,
        "body": body,
        "labels": [],
        "commits": [],
    }


def _decide(
    head: str = HEAD, reviewer: str = REVIEWER, void_at: int | None = None
) -> am.Decision:
    t = _templates()
    comments = [
        {"id": 10, "user": {"login": "jian-hong"}, "body": _fill(t["clear"], head)},
        {"id": 20, "user": {"login": "cursor[bot]"}, "body": _fill(t["agree"], head, reviewer)},
    ]
    if void_at is not None:
        comments.append(
            {"id": void_at, "user": {"login": "jian-hong"}, "body": _fill(t["void"])}
        )
    return am.decide(_pr(_fill(t["pr-body"])), _runs(), comments)


def test_documented_templates_merge():
    assert _decide().action == "merge"


def test_documented_pr_body_declares_writer_family():
    assert am.author_family(_pr(_fill(_templates()["pr-body"]))) == "claude"


def test_documented_templates_on_older_head_refuse():
    assert _decide(head=OLD).action == "skip"


def test_documented_templates_with_short_sha_refuse():
    assert _decide(head=HEAD[:8]).action == "skip"


def test_documented_agree_from_writer_family_refuses():
    assert _decide(reviewer="claude-sonnet-5-5").action == "skip"


@pytest.mark.parametrize(("void_at", "action"), [(30, "skip"), (5, "merge")])
def test_documented_void_voids_only_earlier_stamps(void_at: int, action: str):
    assert _decide(void_at=void_at).action == action
