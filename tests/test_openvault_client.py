"""OpenVault client resilience — canonical paths + legacy aliases."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from CortexOS.integrations import openvault_client


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # silence test output
        return

    def _reply(self, code: int, raw: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/denied":
            self._reply(401, json.dumps({"error": {"message": "that API key is not valid"}}).encode())
        elif self.path == "/broken":
            self._reply(500, b"Internal Server Error")
        else:
            seen = {"auth": self.headers.get("Authorization") or ""}
            self._reply(200, json.dumps({"ok": True, **seen}).encode())


@pytest.fixture()
def loopback_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_request_json_names_status_and_parses_error_body(loopback_server: str) -> None:
    status, body = openvault_client.request_json("GET", "/denied", base=loopback_server)
    assert status == 401
    assert body == {"error": {"message": "that API key is not valid"}}
    status, body = openvault_client.request_json("GET", "/broken", base=loopback_server)
    assert status == 500
    assert body is None


def test_request_json_unreachable_is_zero_and_never_raises() -> None:
    assert openvault_client.request_json("GET", "/x", base="http://127.0.0.1:9", timeout=0.5) == (
        0,
        None,
    )
    assert openvault_client.request_json("GET", "/x", base="http://[::1", timeout=0.5) == (0, None)


def test_request_json_loopback_never_goes_through_a_proxy(
    loopback_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A dead proxy that would swallow the bearer if loopback calls used it.
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    status, body = openvault_client.request_json(
        "GET", "/ok", base=loopback_server, headers={"Authorization": "Bearer ov_test_value"}
    )
    assert status == 200
    assert body is not None and body["auth"] == "Bearer ov_test_value"


def test_base_url_falls_back_to_crew_name_and_names_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("OPENVAULT_BASE_URL", "OPENVAULT_URL", "CREW_OPENVAULT_URL"):
        monkeypatch.delenv(name, raising=False)
    assert openvault_client.openvault_base_url() == "http://127.0.0.1:5000"
    monkeypatch.setenv("CREW_OPENVAULT_URL", "http://127.0.0.1:5001/")
    assert openvault_client.openvault_base_url() == "http://127.0.0.1:5001"
    monkeypatch.setenv("OPENVAULT_BASE_URL", "http://127.0.0.1:5001")
    assert openvault_client.openvault_base_url_conflict() == ""
    monkeypatch.setenv("OPENVAULT_BASE_URL", "http://127.0.0.1:5002")
    conflict = openvault_client.openvault_base_url_conflict()
    assert "OPENVAULT_BASE_URL=http://127.0.0.1:5002" in conflict
    assert "CREW_OPENVAULT_URL=http://127.0.0.1:5001" in conflict


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
