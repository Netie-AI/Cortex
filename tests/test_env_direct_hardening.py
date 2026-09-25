"""Security review of env-direct (``CORTEX_MODEL_TRANSPORT=env-direct``): the fixes.

Each case pins one finding and fails on aa98768:

- P1-A  a relayed caller (``bearer`` given) never spends the operator's env
        keys; proven through the real FastAPI app (``POST /v1/insights`` with
        ``Authorization: Bearer ov_x``) with zero provider requests;
- P2-A  a runtime ``os.environ`` write cannot flip custody (the opt-in latches
        at first read) and the keyvault snapshot may write provider key env
        names only;
- P2-B  no child process inherits a provider key (``child_env`` and the app
        supervisor);
- P2-D  the leave decision rides on the stamp on the allow path;
- P3    redirects refused, bounded body, total deadline, ``HTTPException``
        named, partial keys redacted, LOCAL_ONLY names env-direct as
        cloud-only, ``peek`` follows the latched arming.

Network: the provider transport ``direct_providers._urlopen`` is a recorder
except in the redirect case, which runs the real no-redirect opener against a
loopback HTTP server. Keys are fake.
"""

from __future__ import annotations

import email.message
import http.client
import http.server
import io
import json
import os
import threading
import urllib.error
from collections import deque
from typing import Any

import pytest

from CortexOS.integrations import direct_providers, openvault_client
from CortexOS.integrations import freeroute as fr

KEYS = {
    "GEMINI_API_KEY": "gm-fake-key-1",
    "NVIDIA_API_KEY": "nv-fake-key-2",
    "MISTRAL_API_KEY": "ms-fake-key-3",
    "CEREBRAS_API_KEY": "cb-fake-key-4",
}
ALL_KEY_ENVS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY", "MISTRAL_API_KEY", "CEREBRAS_API_KEY")
GOOGLE = "google:gemini-3-flash-preview"
PREFIX = "NOT OpenVault FreeRoute (env-direct)"
MSGS = [{"role": "user", "content": "how many skus are in stock"}]
RELAY = "env-direct serves in-process callers only; relayed callers are refused"


class _Resp:
    def __init__(self, status: int, payload: bytes) -> None:
        self.status = status
        self._body = io.BytesIO(payload)

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeNet:
    """Provider transport stand-in. Records every request; never connects."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.replies: deque[Any] = deque()

    def __call__(self, req: Any, timeout: float | None = None) -> Any:
        sent = json.loads(req.data.decode("utf-8")) if req.data else {}
        self.requests.append({"url": req.full_url, "body": sent})
        if self.replies:
            got = self.replies.popleft()
            if isinstance(got, BaseException):
                raise got
            if callable(got):
                return got(req)
            return got
        payload = {
            "model": sent.get("model"),
            "choices": [{"message": {"role": "assistant", "content": "SELECT 1"}}],
        }
        return _Resp(200, json.dumps(payload).encode("utf-8"))


@pytest.fixture(autouse=True)
def hermetic(freeroute_hermetic, monkeypatch, tmp_path):
    for name in ALL_KEY_ENVS + (
        direct_providers.TRANSPORT_ENV,
        fr.SWITCH_ENV,
        fr.MODELS_ENV,
        fr.LOCAL_ONLY_ENV,
        "OPENVAULT_BASE_URL",
        "OPENVAULT_URL",
        "CREW_OPENVAULT_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    for p in direct_providers.PROVIDERS:
        monkeypatch.delenv(p.models_env, raising=False)
    monkeypatch.setenv(fr.TOKEN_ENV, "ov_testtoken_abc")
    monkeypatch.setenv(fr.STORE_ENV, str(tmp_path / "hardening_routes.db"))
    # The opt-in is latched per process; freeroute.reset() is the hook that forgets it.
    fr.reset()
    yield
    fr.reset()


@pytest.fixture(autouse=True)
def net(monkeypatch) -> FakeNet:
    fake = FakeNet()
    monkeypatch.setattr(direct_providers, "_urlopen", fake, raising=False)
    # Belt and braces: the stdlib entry point too, so no code path reaches a
    # provider host. Anything else through urllib answers unreachable.
    bases = tuple(p.base for p in direct_providers.PROVIDERS)

    def _stdlib(req: Any, *a: Any, **kw: Any) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.startswith(bases):
            return fake(req, kw.get("timeout", a[1] if len(a) > 1 else None))
        raise urllib.error.URLError("network blocked in test")

    monkeypatch.setattr(direct_providers.urllib.request, "urlopen", _stdlib)
    return fake


@pytest.fixture(autouse=True)
def vault(monkeypatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def _down(method: str, path: str, **_kw: Any) -> tuple[int, None]:
        calls.append((method.upper(), path))
        return 0, None

    monkeypatch.setattr(openvault_client, "request_json", _down)
    return calls


@pytest.fixture()
def keys(monkeypatch) -> dict[str, str]:
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)
    return dict(KEYS)


@pytest.fixture()
def direct(monkeypatch) -> None:
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")


# -- P1-A: relayed callers never spend the operator's env keys -------------------


def test_insights_relayed_bearer_spends_no_operator_key(monkeypatch, tmp_path, direct, keys, net) -> None:
    """``/v1/insights`` relays any ``ov_*`` string; env-direct must refuse it, with zero requests."""
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "0")
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DMS_API_KEYS", "viewer:sk-viewer-test;steward:sk-steward-test;admin:sk-admin-test")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    from fastapi.testclient import TestClient

    from CortexOS.api.app import create_app
    from packs.dms.security.rate_limit import reset_limiter

    reset_limiter(per_minute=240)
    with TestClient(create_app(), client=("10.0.0.5", 5555)) as remote:
        res = remote.post(
            "/v1/insights",
            json={"intent": "how many skus are in stock", "ask": False, "generate": True},
            headers={"Authorization": "Bearer ov_x"},
        )
    assert net.requests == [], "a relayed caller spent the operator's env provider keys"
    # env-direct: an unauthenticated caller is refused before any generate.
    assert res.status_code == 401, res.text
    body = res.json()
    assert body["status"] == "REFUSE"
    assert body["values"] == []
    assert body.get("rows", []) in ([], None)
    raw = json.dumps(body)
    for value in KEYS.values():
        assert value not in raw
    assert "ov_x" not in raw


def _insights_app(monkeypatch, tmp_path):
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "0")
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DMS_API_KEYS", "viewer:sk-viewer-test;steward:sk-steward-test;admin:sk-admin-test")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    from CortexOS.api.app import create_app
    from packs.dms.security.rate_limit import reset_limiter

    reset_limiter(per_minute=240)
    return create_app()


def test_insights_authenticated_caller_is_served_by_env_direct(monkeypatch, tmp_path, direct, keys, net) -> None:
    """A caller the engine auth port admits (DMS sends X-API-Key) is served from env keys."""
    from fastapi.testclient import TestClient

    with TestClient(_insights_app(monkeypatch, tmp_path), client=("10.0.0.5", 5555)) as remote:
        res = remote.post(
            "/v1/insights",
            json={"intent": "how many skus are in stock", "ask": False, "generate": True},
            headers={"X-API-Key": "sk-steward-test", "Authorization": "Bearer sk-steward-test"},
        )
    assert res.status_code == 200, res.text
    assert net.requests, "an authenticated caller got no model call under env-direct"
    assert all(any(r["url"].startswith(p.base) for p in direct_providers.PROVIDERS) for r in net.requests)
    body = res.json()
    raw = json.dumps(body)
    # The fake model answers "SELECT 1", which cannot satisfy the ontology, so the
    # envelope must refuse honestly rather than stamp success.
    assert body["status"] == "REFUSE" and body["values"] == []
    assert body.get("badge") != "green"
    for value in KEYS.values():
        assert value not in raw
    assert "sk-steward-test" not in raw


def test_insights_wrong_api_key_is_refused_under_env_direct(monkeypatch, tmp_path, direct, keys, net) -> None:
    from fastapi.testclient import TestClient

    with TestClient(_insights_app(monkeypatch, tmp_path), client=("10.0.0.5", 5555)) as remote:
        res = remote.post(
            "/v1/insights",
            json={"intent": "how many skus are in stock", "ask": False, "generate": True},
            headers={"X-API-Key": "sk-not-a-key"},
        )
    assert res.status_code == 401, res.text
    assert net.requests == []


@pytest.mark.parametrize("bearer", ["ov_relayedcaller_xyz", ""])
def test_relayed_arming_and_complete_are_refused_with_a_stamp(direct, keys, net, vault, bearer) -> None:
    arm = fr.arming(bearer=bearer)
    assert arm.armed is False and arm.reason == RELAY
    assert arm.public()["custody"] == direct_providers.CUSTODY
    assert fr.arming().armed is True  # the in-process caller is unaffected
    with fr.journal() as stamps:
        out = fr.complete("t", MSGS, pin=GOOGLE, bearer=bearer)
    assert out.ok is False and out.text == ""
    assert out.reason == f"FreeRoute not armed: {RELAY}"
    assert out.stamp is not None and out.stamp.impl == "env-direct"
    assert out.stamp.line() == f"{PREFIX}: FreeRoute t: not sent (FreeRoute not armed: {RELAY})"
    assert fr.last_line(stamps) == out.stamp.line()
    assert net.requests == [] and vault == []


def test_relay_refusal_keeps_the_kill_switch_first(monkeypatch, direct, keys, net) -> None:
    out = fr.complete("t", MSGS, bearer="ov_relayedcaller_xyz")
    assert out.ok is False and out.reason == f"FreeRoute not armed: {RELAY}"
    monkeypatch.setenv(fr.SWITCH_ENV, "0")  # the operator kill switch still wins
    fr.reset()
    out = fr.complete("t", MSGS, bearer="ov_relayedcaller_xyz")
    assert out.ok is False and "CORTEX_FREEROUTE=0" in out.reason and RELAY not in out.reason
    assert net.requests == []


# -- P2-A: a runtime env write cannot claim operator opt-in -------------------------


def test_opt_in_latches_at_first_read(monkeypatch, keys, net, vault) -> None:
    assert direct_providers.enabled() is False  # first read: not opted in
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")  # runtime write
    assert direct_providers.enabled() is False
    out = fr.complete("t", MSGS, pin=GOOGLE)
    assert out.ok is False and "OpenVault unreachable" in out.reason
    assert net.requests == [] and ("GET", "/api/freeroute/status") in vault
    fr.reset()  # the documented hook (a restart) re-reads the env
    assert direct_providers.enabled() is True


def test_opt_in_latched_on_survives_a_runtime_unset(monkeypatch, direct, keys, net) -> None:
    assert direct_providers.enabled() is True
    monkeypatch.delenv(direct_providers.TRANSPORT_ENV)
    assert direct_providers.enabled() is True
    out = fr.complete("t", MSGS, pin=GOOGLE)
    assert out.ok is True and out.text == "SELECT 1"
    assert out.stamp is not None and out.stamp.line().startswith(PREFIX)
    assert len(net.requests) == 1


def test_keyvault_snapshot_cannot_write_the_transport_switch(monkeypatch, keys, net) -> None:
    from CortexOS.execution import workflow_openvault as wov

    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    path_before = os.environ["PATH"]
    snapshot = {
        "ok": True,
        "providers": [
            {"id": "evil", "env": "CORTEX_MODEL_TRANSPORT", "value": "env-direct"},
            {"id": "evil2", "env": "PATH", "value": "/tmp/evil"},
            {"id": "evil3", "env": "CORTEX_FREEROUTE", "value": "1"},
            {"id": "gemini", "value": "gm-from-vault"},
        ],
    }
    monkeypatch.setattr(wov, "_get_json", lambda _path: snapshot)
    got = wov.ensure_provider_keys()
    assert got["ok"] is True
    assert got["hydrated"] == ["GEMINI_API_KEY"]
    assert [r["env"] for r in got["refused"]] == ["CORTEX_MODEL_TRANSPORT", "PATH", "CORTEX_FREEROUTE"]
    assert all(r["note"] == wov.REFUSED_ENV_NOTE for r in got["refused"])
    assert direct_providers.TRANSPORT_ENV not in os.environ
    assert os.environ["PATH"] == path_before
    assert os.environ["GEMINI_API_KEY"] == "gm-from-vault"
    assert "gm-from-vault" not in json.dumps(got)
    assert direct_providers.enabled() is False
    assert fr.complete("t", MSGS, pin=GOOGLE).ok is False and net.requests == []


# -- P2-B: children never inherit a provider key -------------------------------------


def test_child_env_strips_every_provider_key_env() -> None:
    src = {name: f"secret-{i}" for i, name in enumerate(ALL_KEY_ENVS)}
    src.update({fr.TOKEN_ENV: "ov_testtoken_abc", "PATH": "/bin", "HOME": "/h"})
    out = fr.child_env(src)
    assert out == {"PATH": "/bin", "HOME": "/h"}
    assert set(direct_providers.KEY_ENVS) == set(ALL_KEY_ENVS)


def test_app_runner_child_gets_no_provider_key(monkeypatch, keys, tmp_path) -> None:
    from CortexOS.execution import app_runner

    seen: dict[str, Any] = {}

    class _Proc:
        pid = 4242
        stderr = None

        def poll(self) -> None:
            return None

    def fake_popen(argv: list[str], **kwargs: Any) -> _Proc:
        seen.update(kwargs["env"])
        return _Proc()

    monkeypatch.setenv("GOOGLE_API_KEY", "gg-fake-key-5")
    monkeypatch.setattr(app_runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(app_runner, "_port_listening", lambda *a, **k: False)
    monkeypatch.setattr(app_runner, "_wait_port", lambda *a, **k: "listening")
    try:
        got = app_runner.start(
            app_id="hardening-probe",
            stack="python",
            cwd=tmp_path,
            port=8899,
            commands={"start": ["python", "-m", "http.server", "{port}"]},
        )
    finally:
        app_runner._PROCS.pop("hardening-probe", None)
    assert got == {"ok": True, "pid": 4242, "port": 8899}
    assert seen, "spawn env was not captured"
    for name in ALL_KEY_ENVS + (fr.TOKEN_ENV,):
        assert name not in seen, name
    assert seen["PORT"] == "8899"
    assert seen["API_BASE"] == app_runner.DEFAULT_API_BASE
    assert seen["PYTHONUNBUFFERED"] == "1"
    assert seen.get("PATH") == os.environ.get("PATH")


# -- P2-D: the leave decision is on the stamp --------------------------------------


def test_leave_decision_is_recorded_on_the_allow_path(direct, keys, net) -> None:
    out = fr.complete("t", MSGS, pin=GOOGLE, egress="leave")
    assert out.ok is True and out.text == "SELECT 1"
    assert out.stamp is not None
    assert "operator opt-in CORTEX_MODEL_TRANSPORT=env-direct" in out.stamp.leave_gate
    pub = out.stamp.public()
    assert pub["leave_gate"] == out.stamp.leave_gate
    for old in ("call_id", "task", "requested", "served", "impl", "credential", "line", "served_local"):
        assert old in pub
    plain = fr.complete("t", MSGS, pin=GOOGLE)
    assert plain.stamp is not None and plain.stamp.leave_gate == ""


# -- P3: transport hardening ------------------------------------------------------


class _Redirecting(http.server.BaseHTTPRequestHandler):
    hits: list[tuple[str, str, str]] = []

    def _record(self) -> None:
        type(self).hits.append((self.command, self.path, self.headers.get("Authorization") or ""))

    def do_POST(self) -> None:  # noqa: N802
        self._record()
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(302)
        self.send_header("Location", "/stolen")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        body = b'{"model":"x","choices":[{"message":{"content":"SELECT 2"}}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return None


def test_redirect_is_refused_and_the_key_is_not_resent(monkeypatch, direct, keys) -> None:
    """Real opener, loopback server: a 302 is a named refusal, never followed."""
    monkeypatch.setattr(direct_providers, "_urlopen", _real_urlopen())
    _Redirecting.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Redirecting)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        local = direct_providers.Provider(
            "google", ("GEMINI_API_KEY",), f"http://127.0.0.1:{port}/v1", ("gemini-3-flash-preview",)
        )
        monkeypatch.setitem(direct_providers._BY_LABEL, "google", local)
        status, body = direct_providers.request_json(
            "POST", "/v1/chat/completions", body={"model": GOOGLE, "messages": MSGS}, timeout=5.0
        )
    finally:
        server.shutdown()
        server.server_close()
    assert status == 302
    assert body is not None
    assert "redirects are refused" in body["error"]["message"]
    assert [h[:2] for h in _Redirecting.hits] == [("POST", "/v1/chat/completions")]
    assert all(path != "/stolen" for _, path, _ in _Redirecting.hits)


def _real_urlopen() -> Any:
    """The module's own opener (the autouse ``net`` fixture replaced ``_urlopen``)."""
    opener = direct_providers._OPENER
    return lambda req, timeout: opener.open(req, timeout=timeout)


def test_no_redirect_handler_returns_none() -> None:
    handler = direct_providers._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/") is None


def test_oversized_response_is_refused(direct, keys, net) -> None:
    big = b'{"pad":"' + b"a" * direct_providers.MAX_RESPONSE_BYTES + b'"}'
    net.replies.append(_Resp(200, big))
    out = fr.complete("t", MSGS, pin=GOOGLE)
    assert out.ok is False and out.text == ""
    assert f"response larger than {direct_providers.MAX_RESPONSE_BYTES} bytes refused" in out.reason
    assert out.reason.startswith("env-direct provider answered HTTP 0: env-direct: google response larger")
    assert out.stamp is not None and out.stamp.line().startswith(PREFIX)


def test_oversized_error_body_is_refused(direct, keys, net) -> None:
    big = b"x" * (direct_providers.MAX_RESPONSE_BYTES + 1)
    err = urllib.error.HTTPError("https://x", 500, "boom", email.message.Message(), io.BytesIO(big))
    net.replies.append(err)
    status, body = direct_providers.request_json("POST", "/v1/chat/completions", body={"model": GOOGLE})
    assert status == 0 and body is not None
    assert "response larger than" in body["error"]["message"]


def test_total_deadline_bounds_a_slow_drip(monkeypatch, direct, keys, net) -> None:
    clock = [100.0]
    monkeypatch.setattr(direct_providers, "_now", lambda: clock[0])

    class _Drip:
        status = 200

        def read(self, n: int = -1) -> bytes:
            clock[0] += 10.0  # each chunk arrives inside the socket timeout
            return b" "

        def __enter__(self) -> _Drip:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    net.replies.append(_Drip())
    status, body = direct_providers.request_json(
        "POST", "/v1/chat/completions", body={"model": GOOGLE}, timeout=45.0
    )
    assert status == 0 and body is not None
    assert body["error"]["message"] == "env-direct: google total deadline exceeded while reading the response"


def test_http_exception_is_a_named_refusal_not_a_raise(direct, keys, net) -> None:
    net.replies.append(http.client.IncompleteRead(b"partial"))
    status, body = direct_providers.request_json("POST", "/v1/chat/completions", body={"model": GOOGLE})
    assert status == 0 and body is not None
    assert body["error"]["message"] == "google unreachable: IncompleteRead"


@pytest.mark.parametrize("partial", ["AIzaSyA12partial", "nvapi-Ab3d", "csk-9x8y7", "nvapi-****ab12"])
def test_partial_provider_keys_are_redacted_from_reasons(direct, keys, net, partial) -> None:
    raw = json.dumps({"error": {"message": f"API key not valid: {partial}", "type": "bad"}}).encode()
    net.replies.append(
        urllib.error.HTTPError("https://x", 400, "bad", email.message.Message(), io.BytesIO(raw))
    )
    out = fr.complete("t", MSGS, pin=GOOGLE)
    assert out.ok is False
    assert out.reason == "env-direct provider answered HTTP 400: API key not valid: <redacted>"
    assert partial not in json.dumps(out.stamp.public() if out.stamp else {})


def test_local_only_names_env_direct_as_cloud_only(monkeypatch, direct, keys, net) -> None:
    monkeypatch.setenv(fr.LOCAL_ONLY_ENV, "1")
    with fr.journal() as stamps:
        out = fr.complete("t", MSGS, pin=GOOGLE)
    assert out.ok is False and out.text == "" and net.requests == []
    assert out.reason.startswith("CORTEX_FREEROUTE_LOCAL_ONLY=1: env-direct is cloud-only")
    assert "OpenVault" not in out.reason
    assert out.stamp is not None and out.stamp.impl == "env-direct"
    assert out.stamp.served_reason == "env-direct is cloud-only"
    assert fr.last_line(stamps).startswith(PREFIX + ": FreeRoute t: not sent (CORTEX_FREEROUTE_LOCAL_ONLY=1")
    assert fr.arming().public()["local_reason"] == "env-direct is cloud-only"


def test_peek_follows_the_latched_env_direct_arming(monkeypatch, direct, keys, vault) -> None:
    assert fr.arming().armed is True
    monkeypatch.delenv(direct_providers.TRANSPORT_ENV)  # runtime unset: latched on
    got = fr.peek()
    assert got.armed is True and got.custody == direct_providers.CUSTODY
    assert got.reason.startswith("armed env-direct (NOT OpenVault)")
    monkeypatch.setenv(fr.SWITCH_ENV, "0")
    off = fr.peek()
    assert off.armed is False and off.custody == direct_providers.CUSTODY
    assert off.reason == "CORTEX_FREEROUTE=0: FreeRoute disabled by the operator (no fallback)"
    assert vault == []
