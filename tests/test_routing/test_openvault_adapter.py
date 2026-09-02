"""OpenVault loopback adapter never sends a dummy bearer."""

from __future__ import annotations

from CortexOS.routing.adapters.openvault import OpenVaultAdapter, resolved_openvault_token


def test_dummy_env_token_is_empty(monkeypatch):
    monkeypatch.setenv("OPENVAULT_TOKEN", "openvault-loopback")
    assert resolved_openvault_token() == ""
    monkeypatch.setenv("OPENVAULT_TOKEN", "EMPTY")
    assert resolved_openvault_token() == ""
    monkeypatch.delenv("OPENVAULT_TOKEN", raising=False)
    assert resolved_openvault_token() == ""


def test_loopback_headers_omit_authorization():
    adapter = OpenVaultAdapter(api_base="http://127.0.0.1:5000/v1", api_key="openvault-loopback")
    assert "Authorization" not in adapter._headers()
    adapter = OpenVaultAdapter(api_base="http://127.0.0.1:5000/v1", api_key="")
    assert "Authorization" not in adapter._headers()


def test_real_token_is_sent():
    adapter = OpenVaultAdapter(api_base="http://127.0.0.1:5000/v1", api_key="ov-real-key")
    assert adapter._headers()["Authorization"] == "Bearer ov-real-key"


def test_model_strips_openai_prefix():
    assert OpenVaultAdapter._model("openai/auto") == "auto"
    assert OpenVaultAdapter._model("") == "auto"
    assert OpenVaultAdapter._model("gpt-oss-120b") == "gpt-oss-120b"
