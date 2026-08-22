"""Computer-control probe is fail-closed. Distill: connectors capture."""
from __future__ import annotations

from CortexOS.connectors import computer_control


def test_probe_default_not_armed(monkeypatch):
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL", raising=False)
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL_EXECUTE", raising=False)
    status = computer_control.probe()
    assert status["enabled"] is False
    assert status["armed"] is False
    assert status["can_control"] is False
    assert status["uacc_importable"] is False
    ids = {d["id"] for d in status["drivers"]}
    assert ids == {"uacc", "computer-control-mcp", "windows-mcp"}
    win = next(d for d in status["drivers"] if d["id"] == "windows-mcp")
    assert win["imported"] is False
    assert win["windows_only"] is True


def test_invoke_without_flag_fails_closed(monkeypatch):
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL", raising=False)
    out = computer_control.invoke("click", x=10, y=10)
    assert out["ok"] is False
    assert out["executed"] is False
    assert out["error"] == "not_armed"


def test_status_action_is_always_ok(monkeypatch):
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL", raising=False)
    out = computer_control.invoke("status")
    assert out["ok"] is True
    assert out["executed"] is False
    assert out["status"]["armed"] is False


def test_unknown_action_refused():
    out = computer_control.invoke("keylog")
    assert out["ok"] is False
    assert out["error"] == "action_not_allowed"
