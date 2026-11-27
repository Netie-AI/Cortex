"""CORTEX_CALL_DEADLINE: off leaves FreeRoute timeouts and health bytes alone.

On, X-Deadline-Ms caps the 180s crew default, a client disconnect closes the
upstream httpx call, and /health/features counts tasks that otherwise share
crew-think.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import freeroute as crew_fr
from CortexOS.integrations import call_deadline, freeroute, openvault_client

_STATUS = {
    "ok": True,
    "surface": "freeroute",
    "sealed": False,
    "pooled_key_count": 1,
    "hops": [
        {
            "provider": "groq",
            "priority": 10,
            "circuit": "closed",
            "park_until": None,
        }
    ],
    "spendable": [
        {
            "id": "groq",
            "chat_models": ["openai/gpt-oss-120b"],
            "openai_compatible": True,
        }
    ],
    "spendable_count": 1,
}


def _capture(monkeypatch: pytest.MonkeyPatch, fake: Any) -> tuple[list[float], list[str]]:
    timeouts: list[float] = []
    bodies: list[str] = []

    def wrapped(
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 5.0,
        base: str | None = None,
    ) -> tuple[int, dict[str, Any] | None]:
        if path.split("?", 1)[0] == "/v1/chat/completions":
            timeouts.append(float(timeout))
            bodies.append(json.dumps(dict(body or {}), sort_keys=True))
        return fake(
            method, path, body=body, headers=headers, timeout=timeout, base=base
        )

    monkeypatch.setattr(openvault_client, "request_json", wrapped)
    return timeouts, bodies


def test_flag_off_ignores_a_bound_deadline(armed_openvault: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(call_deadline.ENV, raising=False)
    timeouts, bodies = _capture(monkeypatch, armed_openvault)

    async def _twice() -> tuple[dict[str, Any], dict[str, Any]]:
        first_out = await crew_fr.complete(prompt="hello", purpose="think")
        # A deadline and a cancel sitting in context must not move the call.
        deadline = call_deadline._deadline.set(time.monotonic() + 0.05)
        cancel = call_deadline._cancel.set(threading.Event())
        try:
            second_out = await crew_fr.complete(prompt="hello", purpose="think")
        finally:
            call_deadline._deadline.reset(deadline)
            call_deadline._cancel.reset(cancel)
        return first_out, second_out

    first, second = asyncio.run(_twice())
    assert timeouts == [180.0, 180.0]
    assert bodies[0] == bodies[1]
    assert first["live_5000_ci"] is False
    assert second["live_5000_ci"] is False
    assert "call_deadline" not in json.dumps({"ignored": True})


def test_deadline_caps_the_crew_default(armed_openvault: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(call_deadline.ENV, "1")
    call_deadline.reset_counts()
    timeouts, bodies = _capture(monkeypatch, armed_openvault)

    async def _both() -> tuple[dict[str, Any], dict[str, Any]]:
        bare_out = await crew_fr.complete(prompt="hello", purpose="think")
        token = call_deadline._deadline.set(time.monotonic() + 0.4)
        try:
            capped_out = await crew_fr.complete(prompt="hello", purpose="think")
        finally:
            call_deadline._deadline.reset(token)
        return bare_out, capped_out

    bare, capped = asyncio.run(_both())
    assert timeouts[0] == 180.0
    assert 0 < timeouts[1] <= 0.4
    assert bodies[0] == bodies[1]
    assert bare["purpose"] == capped["purpose"] == "think"
    snap = call_deadline.snapshot()
    assert snap["tasks"].get("crew-think") == 2
    assert snap["abandoned"] == 0


def test_improve_count_is_explicit_and_the_pick_task_stays(
    armed_openvault: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The improve step still asks OpenVault on crew-think. The count does not."""
    from CortexOS.crew.freeroute import task_for

    assert task_for("improve") == "crew-think"
    assert task_for("brain") == "crew-think"
    assert task_for("suggest") == "crew-think"
    monkeypatch.setenv(call_deadline.ENV, "1")
    call_deadline.reset_counts()
    seen: list[str] = []
    real = freeroute.complete

    def wrapped(task: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        seen.append(task)
        return real(task, messages, **kwargs)

    monkeypatch.setattr(freeroute, "complete", wrapped)

    async def _once() -> None:
        with call_deadline.count_as(call_deadline.EXPLICIT_TASKS["improve"]):
            await crew_fr.complete(prompt="hello", purpose="think")

    asyncio.run(_once())
    assert seen == ["crew-think"]
    assert call_deadline.snapshot()["tasks"] == {"crew-think-improve": 1}


def test_health_features_stays_the_same_bytes_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(call_deadline.ENV, raising=False)
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("CORTEX_PROFILE", "core")
    monkeypatch.delenv("CORTEX_REQUIRE_AGENTIC_MARKER", raising=False)
    from CortexOS.api.app import create_app

    client = TestClient(create_app(), headers={"X-API-Key": "pytest-admin-key"})
    body = client.get("/health/features").json()
    assert set(body) == {"engine_version", "profile", "extras", "rag"}
    assert "call_deadline" not in body


def test_health_features_reports_task_counts_when_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(call_deadline.ENV, "1")
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("CORTEX_PROFILE", "core")
    monkeypatch.delenv("CORTEX_REQUIRE_AGENTIC_MARKER", raising=False)
    call_deadline.reset_counts()
    call_deadline.note("crew-insights-sql")
    call_deadline.note("crew-think")
    call_deadline.note("crew-think-improve")
    from CortexOS.api.app import create_app

    client = TestClient(create_app(), headers={"X-API-Key": "pytest-admin-key"})
    body = client.get("/health/features").json()
    assert body["call_deadline"] == {
        "tasks": {
            "crew-insights-sql": 1,
            "crew-think": 1,
            "crew-think-improve": 1,
        },
        "abandoned": 0,
    }


class _SlowVault:
    """OpenVault that answers status at once and holds the chat call."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.closed = threading.Event()
        self.full = threading.Event()
        self.stop = threading.Event()
        handler = self._handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        vault = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                if self.path.split("?", 1)[0] != "/api/freeroute/status":
                    self.send_error(404)
                    return
                raw = json.dumps(_STATUS).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                vault.started.set()
                self.connection.settimeout(0.1)
                end = time.monotonic() + 20
                while time.monotonic() < end and not vault.stop.is_set():
                    try:
                        chunk = self.rfile.read(1)
                    except TimeoutError:
                        continue
                    except OSError:
                        vault.closed.set()
                        return
                    if chunk == b"":
                        vault.closed.set()
                        return
                vault.full.set()

        return Handler


def _arm_slow(monkeypatch: pytest.MonkeyPatch, vault: _SlowVault) -> None:
    monkeypatch.setenv(call_deadline.ENV, "1")
    monkeypatch.delenv("CORTEX_FREEROUTE", raising=False)
    for name in ("OPENVAULT_URL", "CREW_OPENVAULT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENVAULT_BASE_URL", vault.url)
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    freeroute.reset()


def test_disconnect_cancels_the_upstream_httpx_call(monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _SlowVault()
    try:
        _arm_slow(monkeypatch, vault)

        class Req:
            def __init__(self) -> None:
                self.headers = {"X-Deadline-Ms": "60000"}
                self.gone = False

            async def is_disconnected(self) -> bool:
                return self.gone

        async def run() -> Any:
            req = Req()
            async with call_deadline.bind_http(req):
                task = asyncio.create_task(crew_fr.complete(prompt="hello", purpose="think"))
                for _ in range(100):
                    if vault.started.is_set():
                        break
                    await asyncio.sleep(0.05)
                assert vault.started.is_set()
                req.gone = True
                return await asyncio.wait_for(task, timeout=5)

        started = time.monotonic()
        result = asyncio.run(run())
        assert time.monotonic() - started < 5
        assert vault.closed.wait(2)
        assert not vault.full.is_set()
        assert result["ok"] is False
        assert call_deadline.snapshot()["abandoned"] >= 1
    finally:
        vault.close()


def test_deadline_returns_before_the_slow_vault_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _SlowVault()
    try:
        _arm_slow(monkeypatch, vault)

        class Req:
            def __init__(self) -> None:
                self.headers = {"X-Deadline-Ms": "400"}
                self.gone = False

            async def is_disconnected(self) -> bool:
                return self.gone

        async def run() -> None:
            req = Req()
            async with call_deadline.bind_http(req):
                await asyncio.wait_for(
                    crew_fr.complete(prompt="hello", purpose="think"),
                    timeout=5,
                )

        started = time.monotonic()
        asyncio.run(run())
        assert time.monotonic() - started < 3
        assert not vault.full.is_set()
        assert call_deadline.snapshot()["abandoned"] == 0
    finally:
        vault.close()


def test_crew_freeroute_header_is_ignored_until_the_flag(
    armed_openvault: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from CortexOS.crew import openvault
    from CortexOS.crew.config import CrewSettings
    from CortexOS.crew.server import create_app

    monkeypatch.setattr(openvault, "ingest_cursor_from_files", lambda root=None: {"ok": False})
    monkeypatch.setattr(openvault, "push_env_keys", lambda: {"ok": True, "skipped": True})
    monkeypatch.setattr(openvault, "disable_seeded_cortex_primary", lambda: {"ok": True})
    monkeypatch.setattr(openvault, "list_vault_keys", lambda **kw: [])
    timeouts, _bodies = _capture(monkeypatch, armed_openvault)
    data = tmp_path / "crew"
    data.mkdir()
    settings = CrewSettings(
        port=0,
        engine_url="http://127.0.0.1:9",
        engine_session="demo",
        data_dir=data,
        master_computer_control=False,
        llm_timeout_s=180,
    )
    app = create_app(settings)
    with TestClient(app, client=("127.0.0.1", 5555)) as client:
        monkeypatch.delenv(call_deadline.ENV, raising=False)
        freeroute.reset()
        off = client.post(
            "/crew/freeroute",
            json={"purpose": "think", "prompt": "hello"},
            headers={"X-Deadline-Ms": "400"},
        )
        assert off.status_code in {200, 409}
        assert timeouts[-1] == 180.0
        monkeypatch.setenv(call_deadline.ENV, "1")
        freeroute.reset()
        on = client.post(
            "/crew/freeroute",
            json={"purpose": "think", "prompt": "hello"},
            headers={"X-Deadline-Ms": "2500"},
        )
        assert on.status_code in {200, 409}
        assert 0 < timeouts[-1] <= 2.5


def test_insights_route_binds_the_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("CORTEX_PROFILE", "core")
    monkeypatch.delenv("CORTEX_REQUIRE_AGENTIC_MARKER", raising=False)
    seen: dict[str, Any] = {}

    async def spy(intent: str, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        seen["cap"] = call_deadline.cap(180.0)
        seen["cancel"] = call_deadline.current_cancel() is not None
        return {"ok": True, "status": "ABSTAIN", "intent": intent}

    monkeypatch.setattr("CortexOS.crew.insights.run_insights", spy)
    from CortexOS.api.app import create_app

    client = TestClient(create_app(), headers={"X-API-Key": "pytest-admin-key"})
    monkeypatch.delenv(call_deadline.ENV, raising=False)
    off = client.post(
        "/v1/insights",
        json={"intent": "how many skus", "ask": False, "generate": False},
        headers={"X-Deadline-Ms": "400"},
    )
    assert off.status_code == 200
    assert seen["cancel"] is False
    assert seen["cap"] == 180.0
    monkeypatch.setenv(call_deadline.ENV, "1")
    on = client.post(
        "/v1/insights",
        json={"intent": "how many skus", "ask": False, "generate": False},
        headers={"X-Deadline-Ms": "2500"},
    )
    assert on.status_code == 200
    assert seen["cancel"] is True
    assert 0 < seen["cap"] <= 2.5
