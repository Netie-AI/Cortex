"""OpenVault client resilience — canonical paths + legacy aliases."""

from __future__ import annotations

from unittest.mock import patch

from CortexOS.integrations import openvault_client


def test_ping_accepts_healthz():
    with patch.object(openvault_client, "get_json", return_value={"status": "ok"}):
        assert openvault_client.ping() is True


def test_ping_accepts_uptime_shape():
    with patch.object(openvault_client, "get_json", return_value={"entries": []}):
        assert openvault_client.ping() is True


def test_freeroute_tries_canonical_then_legacy():
    calls: list[str] = []

    def _fake(path: str, **kwargs: object) -> dict[str, object] | None:
        calls.append(path)
        if path.startswith("/api/openfree/"):
            return {"remaining_tokens": 100}
        return None

    with patch.object(openvault_client, "get_json", side_effect=_fake):
        data = openvault_client.freeroute_ratelimit("wf:test", tier="free")
    assert data is not None
    assert data.get("remaining_tokens") == 100
    assert any("/api/freeroute/" in c for c in calls)
    assert any("/api/openfree/" in c for c in calls)


def test_resolve_access_unreachable_is_explicit():
    with patch.object(openvault_client, "post_json", return_value=None):
        out = openvault_client.resolve_access("memory", "cortex.memory")
    assert out["found"] is False
    assert out["allowed"] is False
    assert "unreachable" in str(out.get("reasons", [""])[0]).lower()


def test_memory_route_has_no_content_field():
    with patch.object(
        openvault_client,
        "post_json",
        return_value={
            "found": True,
            "allowed": True,
            "owner": "cortex",
            "base_url": "http://127.0.0.1:8000",
            "path": "/api/memory/stats",
        },
    ):
        out = openvault_client.memory_route()
    assert "content" not in out


def test_check_openfree_budget_uses_freeroute():
    from CortexOS.execution import workflow_openvault

    with patch.object(
        workflow_openvault,
        "freeroute_ratelimit",
        return_value={"remaining_tokens": 50},
    ):
        out = workflow_openvault.check_openfree_budget("wf:1", prompt_tokens=10, max_tokens=20)
    assert out["ok"] is True
    assert out["remaining"] == 50
