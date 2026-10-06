"""OV-VERIFY-AUTH (#312): ``ov_`` keys verified with Cortex's own OpenVault service bearer.

The fake OpenVault is a real loopback HTTP server, so the assertions are on the
request that actually crossed the wire. It models OpenVault#147 (Refs #135):

* admission is ``X-OpenVault-Admin`` or ``Authorization: Bearer`` whose
  registered service_id is listed, exactly, in ``OPENVAULT_VERIFY_SERVICES``;
  anything else is 401 (unlisted id and wrong bearer look the same);
* live key: ``{ok: true, valid: true, key_id, tier}``; otherwise ``{ok: true, valid: false}``.

Security bars for #312 are tagged ``BAR-1`` .. ``BAR-5`` on the tests that hold them.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from packs.dms.security import api_auth
from packs.dms.security.api_auth import Caller, get_caller, resolve_caller

SERVICE_TOKEN = "svc-cortex-0123456789abcdefSERVICE"
ADMIN_TOKEN = "adm-openvault-0123456789abcdefADMIN"
USER_KEY = "ov_user_live_secret_0123456789"
_ENVS = (
    "OPENVAULT_BASE_URL",
    "OPENVAULT_URL",
    "CREW_OPENVAULT_URL",
    "OPENVAULT_ADMIN_TOKEN_PATH",
    "CORTEX_OV_VERIFY_URL",
    "DMS_API_KEYS",
    "DMS_AUTH_DISABLED",
)
_UNAUTHENTICATED = {"error": {"message": "unauthorized", "type": "openvault_unauthenticated"}}

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes; Windows denies by mode")


def resolve_auth(key: str | None) -> Any:
    return api_auth.resolve_auth(key)


def Deny(reason: str) -> Any:
    return api_auth.Deny(reason)


class FakeOpenVault:
    """OpenVault#147 verify admission plus a recorded request log."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.registered: dict[str, str] = {"cortex": SERVICE_TOKEN}
        self.verify_services: tuple[str, ...] = ("cortex",)
        self.status = 200
        self.body: Any = {"ok": True, "valid": True, "key_id": "k1", "tier": "free"}
        self.delay = 0.0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                fake.requests.append(
                    {"path": self.path, "headers": dict(self.headers.items()), "raw": raw}
                )
                if fake.delay:
                    time.sleep(fake.delay)
                status, body = fake.status, fake.body
                if not fake.admitted(self.headers.get("X-OpenVault-Admin"), self.headers.get("Authorization")):
                    status, body = 401, _UNAUTHENTICATED
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def admitted(self, admin: str | None, authorization: str | None) -> bool:
        if admin and admin == ADMIN_TOKEN:
            return True
        scheme, _, bearer = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not bearer.strip():
            return False
        return any(
            self.registered.get(service_id) == bearer.strip() for service_id in self.verify_services
        )

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _write_secret(path: Path, value: str, mode: int = 0o600) -> Path:
    path.write_text(value, encoding="utf-8")
    os.chmod(path, mode)
    return path


@pytest.fixture
def ov(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeOpenVault]:
    for name in _ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DMS_REFUSE_DEMO_KEYS", "1")
    fake = FakeOpenVault()
    monkeypatch.setenv("CORTEX_OV_VERIFY_URL", fake.url)
    token_file = _write_secret(tmp_path / "cortex_ov_service_token", SERVICE_TOKEN + "\n")
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(token_file))
    try:
        yield fake
    finally:
        fake.close()


def _app() -> TestClient:
    app = FastAPI()

    @app.get("/who")
    async def who(caller: Caller = Depends(get_caller)) -> dict[str, str]:
        return {"actor": caller.actor, "key_id": caller.key_id, "tier": caller.tier}

    return TestClient(app)


# -- allow / deny -------------------------------------------------------------


def test_valid_key_allows_with_key_id_and_tier(ov: FakeOpenVault) -> None:
    decision = resolve_auth(USER_KEY)
    assert decision == Caller(role="viewer", actor="ov_k1", key_id="k1", tier="free")
    assert len(ov.requests) == 1
    assert ov.requests[0]["path"] == "/api/apikeys/verify"
    assert json.loads(ov.requests[0]["raw"]) == {"token": USER_KEY}


def test_valid_key_allows_through_get_caller(ov: FakeOpenVault) -> None:
    res = _app().get("/who", headers={"X-API-Key": USER_KEY})
    assert res.status_code == 200
    assert res.json() == {"actor": "ov_k1", "key_id": "k1", "tier": "free"}


def test_invalid_key_denies_invalid_key(ov: FakeOpenVault) -> None:
    ov.body = {"ok": True, "valid": False}
    assert resolve_auth(USER_KEY) == Deny("invalid_key")
    assert resolve_caller(USER_KEY) is None


def test_valid_false_carries_no_metadata(
    ov: FakeOpenVault, caplog: pytest.LogCaptureFixture
) -> None:
    """BAR-4: a negative verify carries no key_id or tier into the result."""
    caplog.set_level(logging.DEBUG)
    ov.body = {"ok": True, "valid": False, "key_id": "leak-key-id", "tier": "leak-tier"}
    decision = resolve_auth(USER_KEY)
    assert decision == Deny("invalid_key")
    assert [f.name for f in dataclasses.fields(decision)] == ["reason"]
    res = _app().get("/who", headers={"X-API-Key": USER_KEY})
    assert res.status_code == 401
    blob = repr(decision) + res.text + json.dumps(dict(res.headers)) + caplog.text
    assert "leak-key-id" not in blob
    assert "leak-tier" not in blob


def test_ov_401_denies_ov_verify_unauthorized(ov: FakeOpenVault) -> None:
    ov.registered["cortex"] = "a-rotated-service-token"
    assert resolve_auth(USER_KEY) == Deny("ov_verify_unauthorized")
    res = _app().get("/who", headers={"X-API-Key": USER_KEY})
    assert res.status_code == 401
    assert "ov_verify_unauthorized" in res.json()["detail"]


def test_ov_403_denies_ov_verify_forbidden(ov: FakeOpenVault) -> None:
    ov.status = 403
    ov.body = {"detail": "forbidden"}
    assert resolve_auth(USER_KEY) == Deny("ov_verify_forbidden")


def test_ov_429_denies_ov_verify_rate_limited(ov: FakeOpenVault) -> None:
    ov.status = 429
    ov.body = {"detail": "too many verify requests"}
    assert resolve_auth(USER_KEY) == Deny("ov_verify_rate_limited")


def test_ov_500_denies_unexpected_status(ov: FakeOpenVault) -> None:
    ov.status = 500
    assert resolve_auth(USER_KEY) == Deny("ov_verify_unexpected_status")


def test_ov_unreachable_denies(ov: FakeOpenVault) -> None:
    ov.close()
    assert resolve_auth(USER_KEY) == Deny("ov_verify_unreachable")


def test_ov_timeout_denies_unreachable(ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_auth, "_OV_VERIFY_TIMEOUT_S", 0.2)
    ov.delay = 1.0
    assert resolve_auth(USER_KEY) == Deny("ov_verify_unreachable")


def test_fail_closed_reasons_are_distinct(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BAR-3: unreachable, 401 and missing token each deny with their own reason."""
    client = _app()
    reasons = {}

    ov.registered["cortex"] = "not-what-cortex-holds"
    reasons["401"] = resolve_auth(USER_KEY)
    assert client.get("/who", headers={"X-API-Key": USER_KEY}).status_code == 401

    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(tmp_path / "absent"))
    reasons["missing"] = resolve_auth(USER_KEY)
    assert client.get("/who", headers={"X-API-Key": USER_KEY}).status_code == 401

    monkeypatch.setenv(
        "CORTEX_OV_SERVICE_TOKEN_FILE", str(_write_secret(tmp_path / "svc", SERVICE_TOKEN))
    )
    ov.close()
    reasons["unreachable"] = resolve_auth(USER_KEY)
    assert client.get("/who", headers={"X-API-Key": USER_KEY}).status_code == 401

    assert reasons == {
        "401": Deny("ov_verify_unauthorized"),
        "missing": Deny("ov_service_token_missing"),
        "unreachable": Deny("ov_verify_unreachable"),
    }
    assert len({d.reason for d in reasons.values()}) == 3


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        [{"ok": True, "valid": True, "key_id": "k1", "tier": "free"}],
        {},
        {"ok": True},
        {"ok": False, "valid": True, "key_id": "k1", "tier": "free"},
        {"ok": "true", "valid": True, "key_id": "k1", "tier": "free"},
        {"ok": True, "valid": "true", "key_id": "k1", "tier": "free"},
        {"ok": True, "valid": 1, "key_id": "k1", "tier": "free"},
        {"ok": True, "valid": None},
        {"ok": True, "valid": True, "tier": "free"},
        {"ok": True, "valid": True, "key_id": "", "tier": "free"},
        {"ok": True, "valid": True, "key_id": 7, "tier": "free"},
        {"ok": True, "valid": True, "key_id": "k1 ; drop", "tier": "free"},
        {"ok": True, "valid": True, "key_id": "k1"},
        {"ok": True, "valid": True, "key_id": "k1", "tier": ""},
        {"ok": True, "valid": True, "key_id": "k1", "tier": ["free"]},
        {"ok": True, "key": {"key_id": "k1", "tier": "free"}},
    ],
)
def test_malformed_response_denies_ov_verify_malformed(ov: FakeOpenVault, body: Any) -> None:
    ov.body = body
    assert resolve_auth(USER_KEY) == Deny("ov_verify_malformed")


# -- BAR-1: service token custody ---------------------------------------------


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o700, 0o400, 0o666])
def test_service_token_file_must_be_mode_0600(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: int
) -> None:
    """BAR-1: anything but 0600 is a named deny and nothing is sent."""
    loose = _write_secret(tmp_path / "loose", SERVICE_TOKEN, mode)
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(loose))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_bad_mode")
    assert ov.requests == []


def test_service_token_file_mode_is_read_through_a_symlink(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _write_secret(tmp_path / "target", SERVICE_TOKEN, 0o644)
    link = tmp_path / "link"
    link.symlink_to(target)
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(link))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_bad_mode")
    os.chmod(target, 0o600)
    assert isinstance(resolve_auth(USER_KEY), Caller)


def test_service_token_directory_is_not_a_file(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    d = tmp_path / "a_dir"
    d.mkdir(mode=0o700)
    os.chmod(d, 0o600)
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(d))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_not_file")
    assert ov.requests == []


def test_missing_token_file_env_denies_without_http(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BAR-1 / BAR-3."""
    monkeypatch.delenv("CORTEX_OV_SERVICE_TOKEN_FILE")
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_missing")
    assert ov.requests == []


def test_missing_token_file_denies_without_http(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BAR-1 / BAR-3."""
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(tmp_path / "absent"))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_missing")
    assert ov.requests == []


def test_empty_token_file_denies_without_http(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(_write_secret(tmp_path / "e", "  \n")))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_missing")
    assert ov.requests == []


def test_token_with_header_breaking_chars_denies_without_http(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = _write_secret(tmp_path / "bad", "svc-one\nX-OpenVault-Admin: x")
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(bad))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_invalid")
    assert ov.requests == []


def test_bearer_is_exactly_the_service_token_never_admin(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BAR-1: one Authorization Bearer, the service token; no admin header, no admin value."""
    admin = tmp_path / "vault" / "admin_token"
    admin.parent.mkdir()
    _write_secret(admin, ADMIN_TOKEN)
    monkeypatch.setenv("OPENVAULT_ADMIN_TOKEN_PATH", str(admin))
    assert isinstance(resolve_auth(USER_KEY), Caller)
    (sent,) = ov.requests
    headers = {k.lower(): v for k, v in sent["headers"].items()}
    assert headers["authorization"] == f"Bearer {SERVICE_TOKEN}"
    assert "x-openvault-admin" not in headers
    assert "x-api-key" not in headers
    wire = json.dumps(sent["headers"]) + sent["raw"].decode()
    assert ADMIN_TOKEN not in wire
    assert wire.count(SERVICE_TOKEN) == 1


def test_admin_token_file_is_refused_as_service_token(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BAR-1: never the admin token, even at 0600 and even under another name."""
    admin = _write_secret(tmp_path / "custody.secret", ADMIN_TOKEN)
    monkeypatch.setenv("OPENVAULT_ADMIN_TOKEN_PATH", str(admin))
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(admin))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_is_admin")

    default_named = tmp_path / ".openvault" / "admin_token"
    default_named.parent.mkdir()
    _write_secret(default_named, ADMIN_TOKEN)
    monkeypatch.delenv("OPENVAULT_ADMIN_TOKEN_PATH")
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(default_named))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_is_admin")
    assert ov.requests == []


def test_tokens_absent_from_logs_errors_reasons_and_reprs(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """BAR-1: the service bearer and the checked key never surface anywhere."""
    caplog.set_level(logging.DEBUG)
    client = _app()
    seen: list[str] = []
    decisions: list[Any] = []

    def record(decision: Any) -> None:
        decisions.append(decision)
        seen.append(repr(decision))
        seen.append(str(decision))
        seen.append(getattr(decision, "reason", ""))

    for status, body in [
        (200, {"ok": True, "valid": True, "key_id": "k1", "tier": "free"}),
        (403, {"error": {"type": "openvault_forbidden", "echo": SERVICE_TOKEN}}),
        (429, {"error": {"echo": USER_KEY}}),
        (500, {"error": {"echo": SERVICE_TOKEN}}),
        (200, {"ok": True, "valid": True, "key_id": SERVICE_TOKEN + " x", "tier": "free"}),
        (200, {"ok": True, "valid": False, "key_id": SERVICE_TOKEN}),
    ]:
        ov.status, ov.body = status, body
        record(resolve_auth(USER_KEY))
        res = client.get("/who", headers={"X-API-Key": USER_KEY})
        seen.append(res.text)
        seen.append(json.dumps(dict(res.headers)))

    ov.status = 200
    ov.registered["cortex"] = "rotated"
    record(resolve_auth(USER_KEY))

    def leaky(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"transport blew up with {kwargs.get('headers')} {kwargs.get('body')}")

    monkeypatch.setattr("CortexOS.integrations.openvault_client.request_json", leaky)
    record(resolve_auth(USER_KEY))
    res = client.get("/who", headers={"X-API-Key": USER_KEY})
    assert res.status_code == 401
    seen.append(res.text)

    assert isinstance(decisions[0], Caller)
    assert [getattr(d, "reason", None) for d in decisions[1:]] == [
        "ov_verify_forbidden",
        "ov_verify_rate_limited",
        "ov_verify_unexpected_status",
        "ov_verify_malformed",
        "invalid_key",
        "ov_verify_unauthorized",
        "ov_verify_unreachable",
    ]
    assert "openvault key verify denied" in caplog.text
    blob = caplog.text + "\n".join(seen)
    assert SERVICE_TOKEN not in blob
    assert USER_KEY not in blob


# -- BAR-2: loopback-bound :8080 target -----------------------------------------


def _capture_base(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    bases: list[str | None] = []

    def fake(method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        bases.append(kwargs.get("base"))
        return 200, {"ok": True, "valid": True, "key_id": "k1", "tier": "free"}

    monkeypatch.setattr("CortexOS.integrations.openvault_client.request_json", fake)
    return bases


def test_default_target_is_loopback_8080(ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch) -> None:
    """BAR-2."""
    monkeypatch.delenv("CORTEX_OV_VERIFY_URL")
    bases = _capture_base(monkeypatch)
    assert isinstance(resolve_auth(USER_KEY), Caller)
    assert bases == ["http://127.0.0.1:8080"]


def test_configured_loopback_verify_url_is_used(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BAR-2: set via config."""
    monkeypatch.setenv("CORTEX_OV_VERIFY_URL", "http://localhost:8080/")
    bases = _capture_base(monkeypatch)
    assert isinstance(resolve_auth(USER_KEY), Caller)
    assert bases == ["http://localhost:8080"]


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:5000", "http://localhost:5000/", "https://[::1]:5000"]
)
def test_verify_url_port_5000_is_refused(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """BAR-2: never :5000, even on loopback."""
    monkeypatch.setenv("CORTEX_OV_VERIFY_URL", url)
    bases = _capture_base(monkeypatch)
    assert resolve_auth(USER_KEY) == Deny("ov_verify_url_port_5000")
    assert bases == []


@pytest.mark.parametrize(
    "url",
    [
        "http://35.253.229.206:8080",
        "http://35.253.229.206:5001",
        "http://vault.internal:8080",
        "http://0.0.0.0:8080",
        "http://10.128.0.3:8080",
    ],
)
def test_verify_url_must_be_loopback(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """BAR-2: the bearer is only ever sent to a loopback-bound listener."""
    monkeypatch.setenv("CORTEX_OV_VERIFY_URL", url)
    bases = _capture_base(monkeypatch)
    assert resolve_auth(USER_KEY) == Deny("ov_verify_url_not_loopback")
    assert bases == []


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1:8080",
        "http://127.0.0.1",
        "http://user:pw@127.0.0.1:8080",
        "http://127.0.0.1:8080/prefix",
        "http://127.0.0.1:8080?x=1",
        "http://127.0.0.1:99999",
        "127.0.0.1:8080",
    ],
)
def test_verify_url_malformed_is_refused(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("CORTEX_OV_VERIFY_URL", url)
    bases = _capture_base(monkeypatch)
    assert resolve_auth(USER_KEY) == Deny("ov_verify_url_invalid")
    assert bases == []


def test_engine_openvault_url_does_not_steer_verify(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BAR-2: the engine's FreeRoute URL (or its :5000 default) is not the verify target."""
    for name in ("OPENVAULT_BASE_URL", "OPENVAULT_URL", "CREW_OPENVAULT_URL"):
        monkeypatch.setenv(name, "http://127.0.0.1:5000")
    assert isinstance(resolve_auth(USER_KEY), Caller)
    assert len(ov.requests) == 1


def test_auth_module_has_no_5000_default() -> None:
    """BAR-2: no :5000 URL default anywhere in the auth module."""
    source = Path(api_auth.__file__).read_text(encoding="utf-8")
    assert re.search(r"https?://[^\s\"']*:5000", source) is None
    assert "DEFAULT_OPENVAULT_URL" not in source
    assert "openvault_base_url" not in source
    assert api_auth.OV_VERIFY_DEFAULT_URL == "http://127.0.0.1:8080"


# -- BAR-5: service_id "cortex" -----------------------------------------------


def test_service_id_is_exactly_cortex(ov: FakeOpenVault) -> None:
    """BAR-5: the bearer Cortex holds is the one OpenVault registered as ``cortex``."""
    assert api_auth.OV_SERVICE_ID == "cortex"
    ov.registered = {api_auth.OV_SERVICE_ID: SERVICE_TOKEN}
    ov.verify_services = ("cortex",)
    assert isinstance(resolve_auth(USER_KEY), Caller)


@pytest.mark.parametrize(
    ("registered_as", "allowlist"),
    [
        ("Cortex", ("cortex",)),
        ("CORTEX", ("cortex",)),
        ("cortex-dms", ("cortex",)),
        ("dms", ("cortex",)),
        ("cortex", ("Cortex",)),
        ("cortex", ()),
    ],
)
def test_service_id_mismatch_denies(
    ov: FakeOpenVault, registered_as: str, allowlist: tuple[str, ...]
) -> None:
    """BAR-5: OpenVault refuses any id but exactly ``cortex``; Cortex denies by name."""
    ov.registered = {registered_as: SERVICE_TOKEN}
    ov.verify_services = allowlist
    assert resolve_auth(USER_KEY) == Deny("ov_verify_unauthorized")
    res = _app().get("/who", headers={"X-API-Key": USER_KEY})
    assert res.status_code == 401
    assert "ov_verify_unauthorized" in res.json()["detail"]


# -- non-ov keys never reach OpenVault ----------------------------------------


def test_missing_and_unknown_local_keys_are_named(ov: FakeOpenVault) -> None:
    assert resolve_auth(None) == Deny("missing_key")
    assert resolve_auth("") == Deny("missing_key")
    assert resolve_auth("not-an-ov-key") == Deny("invalid_key")
    res = _app().get("/who")
    assert res.status_code == 401
    assert "missing_key" in res.json()["detail"]
    assert ov.requests == []
