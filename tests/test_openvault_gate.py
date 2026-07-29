"""OpenVault gate client — deny-by-default when offline."""

from __future__ import annotations

from unittest.mock import patch

from CortexOS.integrations import openvault_gate


def test_check_gate_allowed():
    with patch.object(
        openvault_gate,
        "post_json",
        return_value={"allowed": True, "keys_ready": True},
    ):
        out = openvault_gate.check_gate(action="run", project_path="/tmp/p")
    assert out["ok"] is True
    assert out["allowed"] is True
    assert "openvault_url" in out


def test_check_gate_offline_deny():
    with patch.object(openvault_gate, "post_json", return_value=None):
        out = openvault_gate.check_gate(action="run")
    assert out["ok"] is False
    assert out["allowed"] is False
    assert out["keys_ready"] is False
    assert any("unreachable" in r.lower() for r in out.get("reasons", []))


def test_resolve_keyvault_snapshot_ok():
    with patch.object(
        openvault_gate,
        "get_json",
        return_value={"providers": [{"id": "openai"}]},
    ):
        out = openvault_gate.resolve_keyvault_snapshot()
    assert out["ok"] is True
    assert out.get("providers")


def test_resolve_keyvault_snapshot_offline():
    with patch.object(openvault_gate, "get_json", return_value=None):
        out = openvault_gate.resolve_keyvault_snapshot()
    assert out["ok"] is False
    assert "error" in out
