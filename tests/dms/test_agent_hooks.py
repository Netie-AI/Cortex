"""P16 agent lifecycle hooks — observability around the SDK write path."""

from __future__ import annotations

import pytest

from CortexOS.agent_sdk import AgentActor, SdkDenied, call_action, clear_agent_hooks, register_agent_hook
from CortexOS.agent_sdk.hooks import _scan_output_secrets

STEWARD = AgentActor(actor="agent_steward", role="steward")
VIEWER = AgentActor(actor="agent_viewer", role="viewer")


@pytest.fixture(autouse=True)
def _clean_hooks():
    clear_agent_hooks()
    yield
    clear_agent_hooks()


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    db = tmp_path / "ops.db"
    monkeypatch.delenv("DMS_LEDGER_DSN", raising=False)
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    return db


def test_before_and_after_fire_on_success(ledger_db, tmp_path, monkeypatch):
    monkeypatch.setattr("netie.execution.tool_runner.OUTPUTS", tmp_path / "outputs")
    seen = []
    register_agent_hook("before_action", lambda ctx: seen.append(("before", ctx["action_id"], ctx["actor"])))
    register_agent_hook("after_action", lambda ctx: seen.append(("after", ctx["result"]["verdict"])))

    call_action("export_pptx", {"title": "Hooked"}, actor=STEWARD, confirmed=True,
                run_id="hk1", db_path=ledger_db, pack="dms")

    assert ("before", "export_pptx", "agent_steward") in seen
    assert ("after", "pass") in seen


def test_on_denied_fires_with_verdict(ledger_db):
    denials = []
    register_agent_hook("on_denied", lambda ctx: denials.append(ctx["verdict"]))
    with pytest.raises(SdkDenied):
        call_action("export_pptx", {"title": "x"}, actor=VIEWER, db_path=ledger_db, pack="dms")
    assert denials == ["rbac"]


def test_hook_exception_never_breaks_the_write_path(ledger_db, tmp_path, monkeypatch):
    monkeypatch.setattr("netie.execution.tool_runner.OUTPUTS", tmp_path / "outputs")

    def boom(ctx):
        raise RuntimeError("bad observer")

    register_agent_hook("before_action", boom)
    register_agent_hook("after_action", boom)
    res = call_action("export_pptx", {"title": "Resilient"}, actor=STEWARD, confirmed=True,
                      run_id="hk2", db_path=ledger_db, pack="dms")
    assert res["ok"] is True  # the raising observers did not break governance


def test_builtin_output_secret_scanner_flags_leaks():
    # the built-in after_action hook runs even after user hooks are cleared
    ctx = {"result": {"ok": True, "note": "leaked ghp_" + "A" * 40}}
    _scan_output_secrets(ctx)
    assert "github_pat" in ctx["result"].get("secrets_flagged", [])

    clean = {"result": {"ok": True, "path": "outputs/x/y/export.pptx"}}
    _scan_output_secrets(clean)
    assert "secrets_flagged" not in clean["result"]


def test_clear_keeps_builtins(ledger_db, tmp_path, monkeypatch):
    monkeypatch.setattr("netie.execution.tool_runner.OUTPUTS", tmp_path / "outputs")
    hit = []
    register_agent_hook("after_action", lambda ctx: hit.append(1))
    clear_agent_hooks()  # drops the user hook, keeps the built-in scanner
    call_action("export_pptx", {"title": "Clean"}, actor=STEWARD, confirmed=True,
                run_id="hk3", db_path=ledger_db, pack="dms")
    assert hit == []  # user hook gone
