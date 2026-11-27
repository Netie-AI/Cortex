"""CORTEX-FREEROUTE-LOCK-01: FreeRoute chat calls overlap, and build_opener stays stdlib.

Collected by the existing ``python -m pytest tests/`` CI step. No new workflow.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from CortexOS.integrations import freeroute as fr

# Captured at import, before any test runs. A nested wrapper would be a
# different object in CortexOS.integrations.freeroute.
_STDLIB_BUILD_OPENER = urllib.request.build_opener

_STATUS = {
    "ok": True,
    "sealed": False,
    "pooled_key_count": 1,
    "hops": [{"provider": "groq", "priority": 10, "circuit": "closed"}],
    "spendable": [{"id": "groq", "chat_models": ["openai/gpt-oss-120b"]}],
}
_CHAT = {
    "model": "openai/gpt-oss-120b",
    "choices": [{"message": {"role": "assistant", "content": "pong"}}],
}
_BUDGET = {"error": {"type": "rate_limited", "message": "budget"}}


class _Server(ThreadingHTTPServer):
    # 50 complete() calls connect at once. The default listen backlog is 5,
    # which drops the rest before the handler runs.
    request_queue_size = 128
    sleep_s: float
    chat_status: int
    retry_after: str
    chat_bodies: list[bytes]
    chat_count: int
    inflight: int
    max_inflight: int
    patched_during: int
    state_lock: threading.Lock

    def __init__(self, sleep_s: float, *, chat_status: int = 200, retry_after: str = "") -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.sleep_s = sleep_s
        self.chat_status = chat_status
        self.retry_after = retry_after
        self.chat_bodies = []
        self.chat_count = 0
        self.inflight = 0
        self.max_inflight = 0
        self.patched_during = 0
        self.state_lock = threading.Lock()
        self.daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _reply(self, code: int, payload: dict[str, Any], extra: dict[str, str] | None = None) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP handler name
        path = self.path.split("?", 1)[0]
        if path == "/api/freeroute/status":
            self._reply(200, _STATUS)
            return
        self._reply(404, {"error": {"message": "unknown"}})

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler name
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b""
        path = self.path.split("?", 1)[0]
        if path != "/v1/chat/completions":
            self._reply(404, {"error": {"message": "unknown"}})
            return
        self._enter(raw)
        try:
            if self.server.sleep_s:
                time.sleep(self.server.sleep_s)
            if self.server.chat_status == 429:
                self._reply(
                    429,
                    _BUDGET,
                    extra={"Retry-After": self.server.retry_after},
                )
                return
            self._reply(200, _CHAT)
        finally:
            self._leave()

    def _enter(self, raw: bytes) -> None:
        server = self.server
        with server.state_lock:
            if urllib.request.build_opener is not _STDLIB_BUILD_OPENER:
                server.patched_during += 1
            server.chat_bodies.append(raw)
            server.chat_count += 1
            server.inflight += 1
            if server.inflight > server.max_inflight:
                server.max_inflight = server.inflight

    def _leave(self) -> None:
        with self.server.state_lock:
            self.server.inflight -= 1


@contextmanager
def _stub(sleep_s: float, *, chat_status: int = 200, retry_after: str = "") -> Iterator[_Server]:
    server = _Server(sleep_s, chat_status=chat_status, retry_after=retry_after)
    thread = threading.Thread(target=server.serve_forever, name="freeroute-lock-stub", daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _enable(monkeypatch: pytest.MonkeyPatch, base: str) -> None:
    monkeypatch.delenv("CORTEX_FREEROUTE", raising=False)
    monkeypatch.delenv("CORTEX_FREEROUTE_TOKEN", raising=False)
    monkeypatch.delenv("CORTEX_FREEROUTE_LOCAL_ONLY", raising=False)
    monkeypatch.delenv("CORTEX_OV_STRICT", raising=False)
    monkeypatch.delenv("CORTEX_CALL_DEADLINE", raising=False)
    for name in ("OPENVAULT_URL", "CREW_OPENVAULT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENVAULT_BASE_URL", base)
    fr.reset()


def _one(timeout: float) -> fr.Completion:
    return fr.complete(
        "freeroute-lock-01",
        [{"role": "user", "content": "lock-01"}],
        timeout=timeout,
    )


def _assert_chat_bytes(raw: bytes) -> None:
    """Wire bytes are json.dumps of the dict, default separators, flag off."""
    parsed = json.loads(raw)
    assert raw == json.dumps(dict(parsed)).encode("utf-8")
    assert parsed["stream"] is False
    assert parsed["max_tokens"] == 600
    assert "strict" not in parsed
    assert parsed["messages"] == [{"role": "user", "content": "lock-01"}]
    assert isinstance(parsed["model"], str) and parsed["model"]


def _assert_opener_is_stdlib() -> None:
    opener = urllib.request.build_opener
    assert opener is _STDLIB_BUILD_OPENER
    assert opener.__module__ == "urllib.request"
    assert opener.__qualname__ == "build_opener"


def test_eight_concurrent_completes_finish_under_four_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """8 calls that each sleep 2s on the stub finish in under 4s (serial would be ~16s)."""
    with _stub(2.0) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _enable(monkeypatch, base)
        arm = fr.arming(fresh=True, timeout=5)
        assert arm.armed, arm.reason
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as pool:
            outs = list(pool.map(lambda _i: _one(10), range(8)))
        elapsed = time.perf_counter() - started
        chat_count = server.chat_count
        max_inflight = server.max_inflight
        patched = server.patched_during
        bodies = list(server.chat_bodies)
    assert patched == 0
    _assert_opener_is_stdlib()
    assert chat_count == 8
    assert max_inflight >= 2
    assert all(out.ok for out in outs), [out.reason for out in outs]
    for raw in bodies:
        _assert_chat_bytes(raw)
    assert elapsed < 4.0, f"8 calls slept 2s each and took {elapsed:.2f}s"


def test_build_opener_is_stdlib_after_fifty_concurrent_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After 50 overlapping complete() calls, build_opener is still the stdlib function."""
    _assert_opener_is_stdlib()
    with _stub(0.0) as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _enable(monkeypatch, base)
        arm = fr.arming(fresh=True, timeout=5)
        assert arm.armed, arm.reason
        with ThreadPoolExecutor(max_workers=50) as pool:
            outs = list(pool.map(lambda _i: _one(5), range(50)))
        patched = server.patched_during
        chat_count = server.chat_count
    assert chat_count == 50
    assert patched == 0
    assert all(out.ok for out in outs), [out.reason for out in outs]
    _assert_opener_is_stdlib()


def test_complete_reads_retry_after_from_the_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real 429 puts the wait on Retry-After. The JSON body has no retry_after_s."""
    with _stub(0.0, chat_status=429, retry_after="4") as server:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        _enable(monkeypatch, base)
        monkeypatch.setenv(fr.STRICT_ENV, "1")
        arm = fr.arming(fresh=True, timeout=5)
        assert arm.armed, arm.reason
        with pytest.raises(fr.FreeRouteStrictError) as caught:
            _one(5)
        assert server.chat_count == 1
    assert caught.value.status == 429
    assert caught.value.retry_after_s == 4
    assert "retry_after_s" not in _BUDGET["error"]
    _assert_opener_is_stdlib()
