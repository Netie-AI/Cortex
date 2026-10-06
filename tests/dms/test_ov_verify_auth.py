"""OV-VERIFY-AUTH (#312): ``ov_`` keys verified with Cortex's own OpenVault service bearer.

The fake OpenVault is a real loopback HTTP server, so the assertions are on the
request that actually crossed the wire. Response shapes follow the verify route
on OpenVault ``cursor/ov-apikey-verify-bind`` (salvaged by OpenVault#135):
``{ok: true, valid: true, key_id, tier, label}`` or ``{ok: true, valid: false}``.
"""

from __future__ import annotations

import json
import logging
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
_URL_ENVS = ("OPENVAULT_BASE_URL", "OPENVAULT_URL", "CREW_OPENVAULT_URL")
_UNAUTHENTICATED = {"error": {"message": "unauthorized", "type": "openvault_unauthenticated"}}


def resolve_auth(key: str | None) -> Any:
    return api_auth.resolve_auth(key)


def Deny(reason: str) -> Any:
    return api_auth.Deny(reason)


class FakeOpenVault:
    """Records every request; answers with ``status`` and ``body`` (bytes or JSON).

    Like OpenVault's HttpGuard, a request without the expected bearer gets 401.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.body: Any = {"ok": True, "valid": True, "key_id": "k1", "tier": "free", "label": "x"}
        self.delay = 0.0
        self.expected_bearer = SERVICE_TOKEN
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
                if self.headers.get("Authorization") != f"Bearer {fake.expected_bearer}":
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
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def ov(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeOpenVault]:
    for name in (*_URL_ENVS, "OPENVAULT_ADMIN_TOKEN_PATH", "DMS_API_KEYS", "DMS_AUTH_DISABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DMS_REFUSE_DEMO_KEYS", "1")
    fake = FakeOpenVault()
    monkeypatch.setenv("OPENVAULT_BASE_URL", fake.url)
    token_file = tmp_path / "cortex_ov_service_token"
    token_file.write_text(SERVICE_TOKEN + "\n", encoding="utf-8")
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


def test_ov_401_denies_ov_verify_unauthorized(ov: FakeOpenVault) -> None:
    ov.expected_bearer = "a-rotated-service-token"
    assert resolve_auth(USER_KEY) == Deny("ov_verify_unauthorized")
    res = _app().get("/who", headers={"X-API-Key": USER_KEY})
    assert res.status_code == 401
    assert "ov_verify_unauthorized" in res.json()["detail"]


def test_ov_403_denies_ov_verify_forbidden(ov: FakeOpenVault) -> None:
    ov.status = 403
    ov.body = {"error": {"message": "unauthorized", "type": "openvault_forbidden"}}
    assert resolve_auth(USER_KEY) == Deny("ov_verify_forbidden")


def test_ov_500_denies_unexpected_status(ov: FakeOpenVault) -> None:
    ov.status = 500
    ov.body = {"ok": True, "valid": True, "key_id": "k1", "tier": "free"}
    assert resolve_auth(USER_KEY) == Deny("ov_verify_unexpected_status")


def test_ov_unreachable_denies(ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch) -> None:
    url = ov.url
    ov.close()
    monkeypatch.setenv("OPENVAULT_BASE_URL", url)
    assert resolve_auth(USER_KEY) == Deny("ov_verify_unreachable")


def test_ov_timeout_denies_unreachable(ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_auth, "_OV_VERIFY_TIMEOUT_S", 0.2)
    ov.delay = 1.0
    assert resolve_auth(USER_KEY) == Deny("ov_verify_unreachable")


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


# -- service token custody ----------------------------------------------------


def test_missing_token_file_env_denies_without_http(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CORTEX_OV_SERVICE_TOKEN_FILE")
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_missing")
    assert ov.requests == []


def test_missing_token_file_denies_without_http(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(tmp_path / "absent"))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_missing")
    assert ov.requests == []


def test_empty_token_file_denies_without_http(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.write_text("  \n", encoding="utf-8")
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(empty))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_missing")
    assert ov.requests == []


def test_token_with_header_breaking_chars_denies_without_http(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = tmp_path / "bad"
    bad.write_text("svc-one\nX-OpenVault-Admin: x", encoding="utf-8")
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(bad))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_invalid")
    assert ov.requests == []


def test_bearer_is_exactly_the_service_token_never_admin(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    admin = tmp_path / "vault" / "admin_token"
    admin.parent.mkdir()
    admin.write_text(ADMIN_TOKEN, encoding="utf-8")
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
    admin = tmp_path / "custody.secret"
    admin.write_text(ADMIN_TOKEN, encoding="utf-8")
    monkeypatch.setenv("OPENVAULT_ADMIN_TOKEN_PATH", str(admin))
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(admin))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_is_admin")

    default_named = tmp_path / ".openvault" / "admin_token"
    default_named.parent.mkdir()
    default_named.write_text(ADMIN_TOKEN, encoding="utf-8")
    monkeypatch.delenv("OPENVAULT_ADMIN_TOKEN_PATH")
    monkeypatch.setenv("CORTEX_OV_SERVICE_TOKEN_FILE", str(default_named))
    assert resolve_auth(USER_KEY) == Deny("ov_service_token_is_admin")
    assert ov.requests == []


def test_tokens_absent_from_logs_and_errors(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = _app()
    seen: list[str] = []
    for status, body in [
        (401, {"error": {"type": "openvault_unauthenticated", "echo": SERVICE_TOKEN}}),
        (403, {"error": {"type": "openvault_forbidden", "echo": USER_KEY}}),
        (200, {"ok": True, "valid": True, "key_id": SERVICE_TOKEN + " x", "tier": "free"}),
        (200, {"ok": True, "valid": False}),
    ]:
        ov.status, ov.body = status, body
        res = client.get("/who", headers={"X-API-Key": USER_KEY})
        assert res.status_code == 401
        seen.append(res.text)
        seen.append(json.dumps(dict(res.headers)))

    def leaky(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"transport blew up with {kwargs.get('headers')} {kwargs.get('body')}")

    monkeypatch.setattr("CortexOS.integrations.openvault_client.request_json", leaky)
    decision = resolve_auth(USER_KEY)
    assert decision == Deny("ov_verify_unreachable")
    seen.append(repr(decision))
    res = client.get("/who", headers={"X-API-Key": USER_KEY})
    assert res.status_code == 401
    seen.append(res.text)

    blob = caplog.text + "\n".join(seen)
    assert "openvault key verify denied" in caplog.text
    assert SERVICE_TOKEN not in blob
    assert USER_KEY not in blob


# -- base URL -----------------------------------------------------------------


def _capture_base(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    bases: list[str | None] = []

    def fake(method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        bases.append(kwargs.get("base"))
        return 200, {"ok": True, "valid": True, "key_id": "k1", "tier": "free"}

    monkeypatch.setattr("CortexOS.integrations.openvault_client.request_json", fake)
    return bases


def test_default_base_is_the_8080_vault_not_5000(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENVAULT_BASE_URL")
    bases = _capture_base(monkeypatch)
    assert isinstance(resolve_auth(USER_KEY), Caller)
    assert bases == ["http://127.0.0.1:8080"]


def test_configured_base_url_is_used(ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_BASE_URL", "http://vault.internal:8080/")
    bases = _capture_base(monkeypatch)
    assert isinstance(resolve_auth(USER_KEY), Caller)
    assert bases == ["http://vault.internal:8080"]


def test_conflicting_base_urls_deny_without_http(
    ov: FakeOpenVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CREW_OPENVAULT_URL", "http://other-vault:8080")
    assert resolve_auth(USER_KEY) == Deny("ov_base_url_conflict")
    assert ov.requests == []


# -- non-ov keys never reach OpenVault ----------------------------------------


def test_missing_and_unknown_local_keys_are_named(ov: FakeOpenVault) -> None:
    assert resolve_auth(None) == Deny("missing_key")
    assert resolve_auth("") == Deny("missing_key")
    assert resolve_auth("not-an-ov-key") == Deny("invalid_key")
    res = _app().get("/who")
    assert res.status_code == 401
    assert "missing_key" in res.json()["detail"]
    assert ov.requests == []
