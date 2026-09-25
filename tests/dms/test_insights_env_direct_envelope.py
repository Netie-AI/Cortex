"""/v1/insights under ``CORTEX_MODEL_TRANSPORT=env-direct``: what the caller receives.

Three defects seen in the live prove, each pinned on the HTTP envelope from the
real app (not on run_insights internals):

1. ``stamp_api`` re-stamped served_* with no stamp, so every envelope said
   ``served_model: None`` even when a model served. The served fields must be
   the generate call's own stamp.
2. When the caller abandons the request, the climb kept calling providers.
   A disconnected client must stop further provider calls.
3. ``/v1/insights/keys`` claimed OpenVault custody under env-direct.

No live network: ``urllib.request.urlopen`` (as direct_providers sees it) is a
recorder, OpenVault reads fail the test, and socket connects fail the test.
The provider key below is fake; the developer's real keys are removed.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.integrations import direct_providers, openvault_client
from CortexOS.integrations import freeroute as core
from packs.dms.security.rate_limit import reset_limiter

FAKE_KEY = "gm-fake-envelope-key-1"
CALLER = "ov_envelopecaller_123"
SQL = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
KEY_ENVS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "NVIDIA_API_KEY",
    "MISTRAL_API_KEY",
    "CEREBRAS_API_KEY",
)


class _Resp:
    def __init__(self, payload: bytes) -> None:
        self.status = 200
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeProvider:
    """``urlopen`` stand-in: counts provider calls, answers like Gemini would."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.first_call = threading.Event()
        # Set by the ASGI receive once it has handed the app http.disconnect.
        self.hung_up = threading.Event()
        self.hold_first_call = False
        self._lock = threading.Lock()

    def __call__(self, req: Any, timeout: float | None = None, **_kw: Any) -> _Resp:
        sent = json.loads(req.data.decode("utf-8")) if req.data else {}
        with self._lock:
            self.requests.append({"url": req.full_url, "body": sent})
        self.first_call.set()
        if self.hold_first_call and len(self.requests) == 1:
            # A real call takes seconds; the caller hangs up while it is in flight.
            self.hung_up.wait(timeout=10)
            time.sleep(0.2)
        return _Resp(
            json.dumps(
                {
                    "id": "chatcmpl-envelope",
                    "model": sent.get("model"),
                    "choices": [{"message": {"role": "assistant", "content": SQL}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                }
            ).encode("utf-8")
        )


@pytest.fixture(autouse=True)
def env_direct(monkeypatch, tmp_path) -> None:
    for name in KEY_ENVS + (core.SWITCH_ENV, core.MODELS_ENV, core.LOCAL_ONLY_ENV):
        monkeypatch.delenv(name, raising=False)
    for p in direct_providers.PROVIDERS:
        monkeypatch.delenv(p.models_env, raising=False)
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "0")
    monkeypatch.setenv(core.STORE_ENV, str(tmp_path / "routes.db"))
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    reset_limiter(per_minute=240)
    core.reset()
    yield
    core.reset()


@pytest.fixture(autouse=True)
def no_vault_no_sockets(monkeypatch, env_direct) -> None:
    # Import the app first: litellm fetches its cost map on import, which is
    # not the code under test. Every socket after this point fails the test.
    import CortexOS.api.app  # noqa: F401

    def _vault(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"env-direct consulted OpenVault: {args!r}")

    def _connect(self, address):  # noqa: ANN001
        raise AssertionError(f"test opened a socket to {address!r}")

    monkeypatch.setattr(openvault_client, "request_json", _vault)
    monkeypatch.setattr(socket.socket, "connect", _connect)


@pytest.fixture()
def provider(monkeypatch) -> FakeProvider:
    fake = FakeProvider()
    monkeypatch.setattr(direct_providers.urllib.request, "urlopen", fake)
    return fake


def _client() -> TestClient:
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_http_envelope_served_fields_are_the_generate_calls_stamp(provider) -> None:
    res = _client().post(
        "/v1/insights",
        json={"intent": "how many skus", "ask": False, "generate": True},
        headers={"Authorization": f"Bearer {CALLER}"},
    )
    assert res.status_code == 200, res.text
    env = res.json()

    # What the customer sees: an abstention with validated SQL and no numbers.
    assert env["status"] == "ABSTAIN"
    assert env["badge"] == "abstain"
    assert env["values"] == []
    assert "Validated SQL via FreeRoute" in env["answer"]
    assert "Numbers not certified" in env["answer"]
    assert "inventory" in (env.get("query_sql") or "").lower()

    # A model served (think + generate), so served_* name it -- not the #272 pending text.
    assert len(provider.requests) == 2
    assert provider.requests[-1]["body"]["model"] == "gemini-3-flash-preview"
    stamp = env["generative"]["stamp"]
    assert stamp["impl"] == direct_providers.IMPL
    assert stamp["served_provider"] == "google"
    assert stamp["served_model"] == "gemini-3-flash-preview"
    assert env["served_provider"] == "google"
    assert env["served_model"] == "gemini-3-flash-preview"
    assert env["served_local"] is False
    assert env["served_reason"] == stamp["served_reason"]
    assert env["served_reason"] != core.SERVED_PENDING_272

    # The route view names the transport that carried the call, not OpenVault.
    route = env["generative"]["route"]
    assert route["connector"] == direct_providers.IMPL
    assert route["custody"] == direct_providers.CUSTODY
    assert "NOT OpenVault FreeRoute (env-direct)" in stamp["line"]

    assert FAKE_KEY not in res.text
    assert CALLER not in res.text


@pytest.mark.asyncio
async def test_disconnected_client_gets_no_further_provider_calls(provider) -> None:
    """The client leaves during the first model call; no second call is made."""
    from CortexOS.api.app import create_app

    provider.hold_first_call = True
    app = create_app()
    payload = json.dumps({"intent": "how many skus", "ask": False, "generate": True}).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/insights",
        "raw_path": b"/v1/insights",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
            (b"authorization", f"Bearer {CALLER}".encode()),
        ],
        "client": ("10.0.0.5", 5555),
        "server": ("testserver", 80),
    }
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": payload, "more_body": False}
        # The caller hangs up once the first provider call is on the wire.
        while not provider.first_call.is_set():
            await asyncio.sleep(0.005)
        provider.hung_up.set()
        return {"type": "http.disconnect"}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await asyncio.wait_for(app(scope, receive, send), timeout=60)

    # Only the call that was in flight when the caller left; nothing after it.
    assert len(provider.requests) == 1, [r["body"].get("model") for r in provider.requests]

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200
    body = json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))
    assert body["status"] == "REFUSE"
    assert body["values"] == []
    assert body["client_disconnected"] is True
    assert "client disconnected" in body["generative"]["refuse_reason"]
    assert body["generative"]["climb"]["final"] == "GENERATE_REFUSE"


def test_key_posture_reports_env_direct_custody(provider) -> None:
    client = _client()
    res = client.get("/v1/insights/keys")
    assert res.status_code == 200, res.text
    pose = res.json()
    assert pose["custody"] == direct_providers.CUSTODY
    assert pose["custody"] != "openvault"
    assert pose["transport"] == direct_providers.IMPL
    assert pose["process_env_provider_keys"] is True
    assert pose["armed"] is True
    assert pose["cloud_hops"] == ["google"]
    assert pose["key_envs"] == ["GEMINI_API_KEY"]
    assert pose["vault_url_loopback"] is None
    assert "NOT OpenVault custody" in pose["note"]
    assert "Cortex never stores provider secrets" not in pose["note"]
    assert pose["callers_hold_provider_keys"] is False
    assert pose["token_returned"] is False
    assert FAKE_KEY not in res.text

    ident = client.get("/v1/insights/identity")
    assert ident.status_code == 200, ident.text
    assert ident.json()["custody"] == direct_providers.CUSTODY
    assert FAKE_KEY not in ident.text
    assert provider.requests == []  # posture never spends
