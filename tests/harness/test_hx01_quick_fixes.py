"""HX-01 quick safety fixes: cot_climb credential, broker SSRF guard, decision_log mode.

PRD R3.4, R4.6 and R5.5. Stubbed only: no network beyond a loopback test server.
"""

from __future__ import annotations

import asyncio
import os
import stat
from typing import Any

import pytest

# -- R3.4: a TypeError never re-sends without the caller's bearer ---------------


def test_cot_climb_type_error_is_one_attempt_never_retried_without_bearer() -> None:
    from CortexOS.crew import cot_climb

    calls: list[dict[str, Any]] = []

    async def runner(_req: Any, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        if "bearer" in kw:
            raise TypeError("unexpected keyword argument 'bearer'")
        return {"ok": True, "text": "answered on Cortex's own credential"}

    out = asyncio.run(
        cot_climb._call_runner(runner, purpose="sql", prompt="q", bearer="ov_caller_token_123")
    )
    assert len(calls) == 1, f"retried: {calls}"
    assert all(c.get("bearer") == "ov_caller_token_123" for c in calls)
    assert out["ok"] is False
    assert "not retried without the caller's credential" in out["reason"]


# -- R4.6: the agent broker forces public_only ----------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/admin",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://localhost:5000/api/keys",
    ],
)
def test_broker_forces_public_only_whatever_the_model_passes(monkeypatch, url) -> None:
    from CortexOS.execution import agent_task, web_tools

    fetched: list[str] = []

    def record(target: str, *a: Any, **k: Any) -> bytes:
        fetched.append(target)
        return b"<html><title>internal</title>secret</html>"

    monkeypatch.setattr(web_tools, "_get", record)
    monkeypatch.setattr(web_tools, "_get_public", record)
    for params in ({"url": url}, {"url": url, "public_only": False}):
        out = agent_task.default_broker("web_fetch", params)
        assert out["ok"] is False and out["error"].startswith("refused:"), out
    assert fetched == []


def test_broker_still_fetches_a_public_url(monkeypatch) -> None:
    from CortexOS.execution import agent_task, web_tools

    fetched: list[str] = []
    monkeypatch.setattr(web_tools, "non_public_reason", lambda url: "")
    monkeypatch.setattr(web_tools, "_get", lambda *a, **k: pytest.fail("unguarded fetch used"))
    monkeypatch.setattr(
        web_tools, "_get_public", lambda target, **k: fetched.append(target) or b"<title>ok</title>hello"
    )
    out = agent_task.default_broker("web_fetch", {"url": "https://example.com/"})
    assert out["ok"] is True and out["title"] == "ok"
    assert fetched == ["https://example.com/"]


def test_non_public_reason_refuses_a_bad_port_instead_of_raising() -> None:
    from CortexOS.execution import web_tools

    assert "bad url" in web_tools.non_public_reason("http://example.com:99999/")


def test_non_public_reason_allows_a_public_literal() -> None:
    from CortexOS.execution import web_tools

    assert web_tools.non_public_reason("http://10.0.0.8/") != ""
    assert web_tools.non_public_reason("http://localhost:8010/") != ""
    assert web_tools.non_public_reason("https://1.1.1.1/") == ""


def test_fetch_refuses_when_the_connected_peer_is_loopback(monkeypatch) -> None:
    """DNS rebinding: the name checks out as public, then the socket lands on
    127.0.0.1. The connected peer is re-checked before any request is sent."""
    import http.server
    import threading

    from CortexOS.execution import web_tools

    hits: list[str] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<title>secret</title>vault keys")

        def log_message(self, *a: object) -> None:
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(web_tools, "non_public_reason", lambda url: "")
        page = web_tools.fetch(f"http://127.0.0.1:{srv.server_port}/", public_only=True)
    finally:
        srv.shutdown()
    assert page["ok"] is False
    assert "non-public address 127.0.0.1" in page["error"]
    assert hits == []


# -- R5.5: the decision log is 0600 ---------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
@pytest.mark.parametrize("existing", [False, True])
def test_decision_log_file_is_0600(tmp_path, existing) -> None:
    from CortexOS.decision import decision_log

    path = tmp_path / "tier_decisions.jsonl"
    if existing:
        path.write_text("", encoding="utf-8")
        os.chmod(path, 0o644)
    old = os.umask(0o022)
    try:
        decision_log._write_line(path, '{"x": 1}')
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text(encoding="utf-8") == '{"x": 1}\n'
