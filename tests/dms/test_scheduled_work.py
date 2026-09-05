"""Operator scheduled work: gh/ship-gate/mail, not prompt echo."""

from __future__ import annotations

import pytest

from CortexOS.execution import routine_composer, scheduled_work
from CortexOS.execution import routine_scheduler as rs


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from CortexOS.execution import action_event, action_value, scoreboard

    monkeypatch.setattr(rs, "DB_PATH", tmp_path / "routines.db")
    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    monkeypatch.setattr(action_event, "DB_PATH", tmp_path / "action_events.db")
    monkeypatch.setattr(action_value, "DB_PATH", tmp_path / "action_value.db")
    rs.init()
    scoreboard.init()


def test_classify_open_prs_is_github():
    kinds = scheduled_work.classify("Summarize my open PRs every weekday morning")
    assert "github" in kinds
    assert scheduled_work.is_operator_work(kinds)


def test_classify_hello_is_not_operator_work():
    kinds = scheduled_work.classify("hello routine")
    assert kinds == frozenset({"web"})
    assert not scheduled_work.is_operator_work(kinds)


def test_classify_email_and_ship_gate():
    assert "email" in scheduled_work.classify("Email me a daily inbox digest")
    assert "build" in scheduled_work.classify("Run the AirGPT ship-gate every weekday")
    assert "pr_create" in scheduled_work.classify("open a pull request for the current branch daily")


def test_draft_names_github_work():
    draft = routine_composer.compose("Summarize my open PRs every weekday morning")
    assert "github" in draft["work_kinds"]
    assert any("gh" in a.lower() or "pr" in a.lower() for a in draft["assumptions"])


def test_github_run_is_not_prompt_echo():
    listed = {
        "ok": True,
        "prs": [
            {
                "number": 12,
                "title": "crew drop",
                "url": "https://example.test/12",
                "review": "REVIEW_REQUIRED",
            }
        ],
        "law": "Do not auto-merge.",
    }
    out = scheduled_work.run(
        "Summarize my open PRs every weekday morning",
        list_prs_fn=lambda **k: listed,
    )
    assert out["ok"] is True
    assert "crew drop" in out["output"]
    assert "Summarize my open PRs every weekday morning" != out["output"]
    assert out["steps"][0]["tool"] == "gh_pr_list"
    assert out["chosen"] == "scheduled_work"


def test_review_fetches_diff():
    listed = {
        "ok": True,
        "prs": [{"number": 4, "title": "fix", "url": "https://x/4", "repo": "acme/x"}],
    }
    out = scheduled_work.run(
        "code review open PRs daily",
        list_prs_fn=lambda **k: listed,
        pr_diff_fn=lambda **k: {"ok": True, "diff": "+def foo():\n+    return 1\n"},
    )
    assert out["ok"] is True
    assert any(s["tool"] == "gh_pr_diff" and s["ok"] for s in out["steps"])
    assert "def foo" in out["output"]


def test_email_digest_sends_when_injected():
    inbox = {
        "ok": True,
        "messages": [{"from": "a@b", "subject": "hello", "date": "Sat"}],
    }
    sent = {"ok": True, "sent": True, "to": "ops@example.test"}
    out = scheduled_work.run(
        "Email me a daily inbox digest",
        inbox_fn=lambda **k: inbox,
        mail_send_fn=lambda subject, body: sent,
    )
    assert out["ok"] is True
    assert "hello" in out["output"]
    assert any(s["tool"] == "smtp_send" and s["ok"] for s in out["steps"])


def test_email_without_creds_still_records_the_attempt():
    out = scheduled_work.run(
        "Email me a daily inbox digest",
        inbox_fn=lambda **k: {"ok": False, "messages": [], "detail": "no IMAP"},
        mail_send_fn=lambda *_a: {"ok": False, "sent": False, "error": "no_smtp"},
    )
    assert out["ok"] is False
    assert any(s["tool"] == "imap_fetch" for s in out["steps"])


@pytest.mark.asyncio
async def test_pr_routine_dispatch_uses_github(monkeypatch):
    monkeypatch.setattr(
        "CortexOS.execution.scheduled_work.run",
        lambda prompt, kinds=None, **k: {
            "ok": True,
            "status": "ok",
            "output": "Open pull requests:\n- #9 real work",
            "error": "",
            "steps": [{"tool": "gh_pr_list", "ok": True, "summary": "1 open PRs"}],
            "work_kinds": ["github"],
            "chosen": "scheduled_work",
        },
    )
    routine = rs.create_from_goal("Summarize my open PRs every weekday morning")
    rs.update_routine(routine["id"], next_run_at=0)
    out = await rs.run_once(routine["id"])
    assert out["ok"] is True
    assert out["chosen"] == "scheduled_work"
    assert out["steps"][0]["tool"] == "gh_pr_list"
    runs = rs.list_runs(routine["id"])
    assert "real work" in (runs[0]["output"] or "")
    listed_rt = rs.list_routines()
    assert listed_rt[0]["last_run"]["steps"][0]["tool"] == "gh_pr_list"
