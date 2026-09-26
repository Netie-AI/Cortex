"""ENV-DIRECT (b75b3b5): the ``CORTEX_MODEL_TRANSPORT=env-direct`` contract.

The opt-in replaces OpenVault custody with provider keys from the process env,
so every case here pins one edge of that swap:

- only the exact opt-in enables it; anything else stays on OpenVault;
- arming names the key env vars it armed from (or would need), and says NOT OpenVault;
- each provider gets its own env key as the bearer, and the OpenVault ``ov_``
  token (Cortex's or a relayed caller's) never reaches a third-party host;
- stamps say ``NOT OpenVault FreeRoute (env-direct)`` and carry served_* fields;
- a provider refusal is named, never poisons the OpenVault credential cache,
  and never falls back to another provider inside the call.

No live network: ``direct_providers._urlopen`` (its one network call) is a
recorder, OpenVault reads are a recorder that answers unreachable, and any
socket connect fails the test. The developer's real provider keys are removed;
the keys below are fake and short enough that ``freeroute.redact`` leaves them
visible, so a leak would be seen rather than masked.
"""

from __future__ import annotations

import email.message
import io
import json
import socket
import urllib.error
from collections import deque
from typing import Any

import pytest

from CortexOS.integrations import direct_providers, openvault_client
from CortexOS.integrations import freeroute as fr

OV_TOKEN = "ov_testtoken_abc"
RELAYED = "ov_relayedcaller_xyz"
KEYS = {
    "GEMINI_API_KEY": "gm-fake-key-1",
    "NVIDIA_API_KEY": "nv-fake-key-2",
    "MISTRAL_API_KEY": "ms-fake-key-3",
    "CEREBRAS_API_KEY": "cb-fake-key-4",
}
ALL_KEY_ENVS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "NVIDIA_API_KEY",
    "MISTRAL_API_KEY",
    "CEREBRAS_API_KEY",
)
# Written out, not read from PROVIDERS, so a changed base URL fails here.
ROUTES = {
    "google": (
        "google:gemini-3-flash-preview",
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "GEMINI_API_KEY",
    ),
    "nvidia": (
        "nvidia:moonshotai/kimi-k3",
        "https://integrate.api.nvidia.com/v1/chat/completions",
        "NVIDIA_API_KEY",
    ),
    "mistral": (
        "mistral:mistral-medium-latest",
        "https://api.mistral.ai/v1/chat/completions",
        "MISTRAL_API_KEY",
    ),
    "cerebras": (
        "cerebras:gpt-oss-120b",
        "https://api.cerebras.ai/v1/chat/completions",
        "CEREBRAS_API_KEY",
    ),
}
PREFIX = "NOT OpenVault FreeRoute (env-direct)"
MSGS = [{"role": "user", "content": "how many skus are in stock"}]


# -- fakes ----------------------------------------------------------------------


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
    """``direct_providers._urlopen`` stand-in. Records every request; never connects."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.replies: deque[tuple[int, Any]] = deque()
        self.content = "SELECT COUNT(*) FROM inventory"

    def reply(self, status: int = 200, body: Any = None, *, raw: bytes | None = None) -> FakeNet:
        self.replies.append((status, raw if raw is not None else json.dumps(body).encode("utf-8")))
        return self

    def fail(self, exc: BaseException) -> FakeNet:
        self.replies.append((0, exc))
        return self

    def __call__(self, req: Any, timeout: float | None = None, **_kw: Any) -> _Resp:
        sent = json.loads(req.data.decode("utf-8")) if req.data else {}
        self.requests.append(
            {
                "url": req.full_url,
                "method": req.get_method(),
                "headers": {k.lower(): v for k, v in req.header_items()},
                "raw": req.data or b"",
                "body": sent,
                "timeout": timeout,
            }
        )
        if self.replies:
            status, payload = self.replies.popleft()
        else:
            status, payload = 200, json.dumps(
                {
                    "id": "chatcmpl-1",
                    "model": sent.get("model"),
                    "choices": [{"message": {"role": "assistant", "content": self.content}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                }
            ).encode("utf-8")
        if isinstance(payload, BaseException):
            raise payload
        if status != 200:
            raise urllib.error.HTTPError(
                req.full_url, status, "refused", email.message.Message(), io.BytesIO(payload)
            )
        return _Resp(status, payload)

    @property
    def urls(self) -> list[str]:
        return [r["url"] for r in self.requests]


class VaultDown:
    """``openvault_client.request_json`` stand-in: records, answers unreachable."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, **_kw: Any) -> tuple[int, None]:
        self.calls.append((method.upper(), path))
        return 0, None


@pytest.fixture(autouse=True)
def env_direct_hermetic(freeroute_hermetic, monkeypatch, tmp_path):
    """Runs after the suite's hermetic fixture: no real keys, no mode, fresh caches."""
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
    monkeypatch.setenv(fr.TOKEN_ENV, OV_TOKEN)
    monkeypatch.setenv(fr.STORE_ENV, str(tmp_path / "env_direct_routes.db"))
    fr.reset()
    yield
    fr.reset()


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    attempts: list[Any] = []

    def _refuse(self, address):  # noqa: ANN001
        attempts.append(address)
        raise AssertionError(f"env-direct test opened a socket to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    yield attempts
    assert attempts == [], f"live network attempted: {attempts}"


@pytest.fixture(autouse=True)
def net(monkeypatch) -> FakeNet:
    fake = FakeNet()
    monkeypatch.setattr(direct_providers, "_urlopen", fake)
    return fake


@pytest.fixture(autouse=True)
def vault(monkeypatch) -> VaultDown:
    fake = VaultDown()
    monkeypatch.setattr(openvault_client, "request_json", fake)
    return fake


@pytest.fixture(autouse=True)
def gate(monkeypatch) -> list[dict[str, Any]]:
    """OpenVault leave gate stand-in: records every consult and denies."""
    calls: list[dict[str, Any]] = []

    def _deny(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "allowed": False, "reasons": ["vault is sealed"]}

    monkeypatch.setattr("CortexOS.integrations.openvault_gate.check_gate", _deny)
    return calls


@pytest.fixture()
def keys(monkeypatch) -> dict[str, str]:
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)
    return dict(KEYS)


@pytest.fixture()
def direct(monkeypatch) -> None:
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")


def _secrets_in(record: dict[str, Any]) -> str:
    return json.dumps(record["headers"]) + record["raw"].decode("utf-8")


# -- 1. only the exact opt-in enables it ------------------------------------------


@pytest.mark.parametrize(
    "value", [None, "", "direct", "1", "true", "yes", "env_direct", "envdirect", "openvault"]
)
def test_anything_but_the_opt_in_stays_on_openvault(monkeypatch, keys, net, vault, value) -> None:
    if value is not None:
        monkeypatch.setenv(direct_providers.TRANSPORT_ENV, value)
    assert direct_providers.enabled() is False
    arm = fr.arming(fresh=True)
    assert arm.armed is False
    assert arm.reason == "OpenVault unreachable at http://127.0.0.1:5000"
    assert arm.public()["custody"] == "openvault"
    assert "NOT OpenVault" not in arm.reason
    assert ("GET", "/api/freeroute/status") in vault.calls
    send, impl = fr._transport()
    assert send is openvault_client.request_json and send is vault
    assert impl == fr.IMPL
    out = fr.complete("t", MSGS, pin=ROUTES["google"][0])
    assert out.ok is False and "OpenVault unreachable" in out.reason
    assert net.requests == []  # provider keys in env never reach a provider


@pytest.mark.parametrize("value", ["1", "direct", "true"])
def test_non_opt_in_values_send_through_openvault_even_with_keys(
    monkeypatch, keys, net, armed_openvault, value
) -> None:
    """Armed OpenVault + provider keys + a near-miss value: the chat goes to OpenVault."""
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, value)
    armed_openvault.identities[OV_TOKEN] = "key_cortex"
    out = fr.complete("t", MSGS)
    assert out.ok is True, out.reason
    assert len(armed_openvault.chat_calls) == 1
    assert armed_openvault.chat_calls[0]["headers"] == {"Authorization": f"Bearer {OV_TOKEN}"}
    assert net.requests == []
    assert out.stamp.impl == fr.IMPL
    assert not out.stamp.line().startswith("NOT OpenVault")


@pytest.mark.parametrize("value", ["env-direct", "ENV-DIRECT ", " Env-Direct"])
def test_opt_in_is_trimmed_and_case_insensitive(monkeypatch, keys, vault, value) -> None:
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, value)
    assert direct_providers.enabled() is True
    assert fr.arming().armed is True
    send, impl = fr._transport()
    assert send is direct_providers.request_json and impl == "env-direct"
    assert vault.calls == []


# -- 2. enabled without a key -------------------------------------------------------


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_opt_in_without_a_provider_key_is_unarmed_and_names_the_env_vars(
    monkeypatch, direct, net, vault, blank
) -> None:
    if blank is not None:
        for name in ALL_KEY_ENVS:
            monkeypatch.setenv(name, blank)
    arm = fr.arming()
    assert arm.armed is False
    assert arm.reason.startswith("CORTEX_MODEL_TRANSPORT=env-direct but no provider key is set")
    for name in ALL_KEY_ENVS:
        assert name in arm.reason
    assert arm.public()["custody"] == direct_providers.CUSTODY
    out = fr.complete("t", MSGS)
    assert out.ok is False
    assert out.reason.startswith("FreeRoute not armed: CORTEX_MODEL_TRANSPORT=env-direct")
    assert net.requests == [] and vault.calls == []


# -- 3. enabled with keys -----------------------------------------------------------


def test_opt_in_with_keys_arms_not_openvault(monkeypatch, direct, vault) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEYS["GEMINI_API_KEY"])
    monkeypatch.setenv("NVIDIA_API_KEY", KEYS["NVIDIA_API_KEY"])
    arm = fr.arming()
    assert arm.armed is True
    assert arm.reason == (
        "armed env-direct (NOT OpenVault): google via GEMINI_API_KEY, nvidia via NVIDIA_API_KEY"
    )
    pub = arm.public()
    assert pub["custody"] != "openvault" and pub["custody"] == direct_providers.CUSTODY
    assert pub["url"] == "env-direct:"
    assert [h["provider"] for h in pub["hops"]] == ["google", "nvidia"]
    assert all(h["served_local"] is False for h in pub["hops"])
    models, source = fr.candidates(arm)
    assert models == ("google:gemini-3-flash-preview", "nvidia:moonshotai/kimi-k3")
    assert source == "live hops x env-direct catalogue"
    for model in models:
        provider, bare = direct_providers.split_id(model)
        assert provider is not None and model == f"{provider.label}:{bare}"
    status = fr.public_status()
    assert status["layer"] == "env-direct provider keys (NOT OpenVault)"
    assert status["impl"] == "env-direct"
    assert status["arming"]["custody"] == direct_providers.CUSTODY
    assert vault.calls == []


def test_google_api_key_is_the_fallback_env(monkeypatch, direct, net) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "gg-fake-key-5")
    arm = fr.arming()
    assert arm.armed is True and "google via GOOGLE_API_KEY" in arm.reason
    out = fr.complete("t", MSGS)
    assert out.ok is True, out.reason
    assert net.requests[0]["headers"]["authorization"] == "Bearer gg-fake-key-5"


def test_per_provider_model_override(monkeypatch, direct, keys) -> None:
    monkeypatch.setenv("CORTEX_DIRECT_GOOGLE_MODELS", " gemini-a, gemini-b ,gemini-a,, ")
    monkeypatch.setenv("CORTEX_DIRECT_NVIDIA_MODELS", "meta/llama-x")
    arm = fr.arming()
    catalogue = dict(arm.catalogue)
    assert catalogue["google"] == ("google:gemini-a", "google:gemini-b")
    assert catalogue["nvidia"] == ("nvidia:meta/llama-x",)
    assert catalogue["mistral"] == ("mistral:mistral-medium-latest",)  # untouched default
    models, _ = fr.candidates(arm)
    assert models[:3] == ("google:gemini-a", "google:gemini-b", "nvidia:meta/llama-x")


# -- 4. the right host, the right key, never the OpenVault token -----------------


@pytest.mark.parametrize("label", sorted(ROUTES))
@pytest.mark.parametrize("bearer", [None, RELAYED, ""])
def test_each_provider_gets_its_own_env_key_and_never_the_ov_token(
    direct, keys, net, vault, label, bearer
) -> None:
    pin, url, key_env = ROUTES[label]
    out = fr.complete("t", MSGS, pin=pin, bearer=bearer)
    if bearer is not None:
        # A relayed caller never spends the operator's env keys (security review P1-A).
        assert out.ok is False
        assert out.reason == f"FreeRoute not armed: {direct_providers.RELAY_REFUSED}"
        assert net.requests == [] and vault.calls == []
        return
    assert out.ok is True, out.reason
    assert len(net.requests) == 1
    sent = net.requests[0]
    assert sent["url"] == url
    assert sent["method"] == "POST"
    assert sent["headers"]["authorization"] == f"Bearer {keys[key_env]}"
    assert sent["body"]["model"] == pin.split(":", 1)[1]
    assert sent["body"]["messages"] == MSGS
    assert "metadata" not in sent["body"] and "local_only" not in sent["body"]
    blob = _secrets_in(sent)
    assert OV_TOKEN not in blob and "testtoken" not in blob
    assert RELAYED not in blob and "ov_" not in blob
    for other_env, other_key in keys.items():
        if other_env != key_env:
            assert other_key not in blob
    public = json.dumps(out.stamp.public())
    assert keys[key_env] not in public and OV_TOKEN not in public
    assert out.stamp.credential == "process-env provider key"
    assert vault.calls == []


# -- 5. stamps -----------------------------------------------------------------------


def test_stamp_is_labelled_and_served_fields_are_namespaced(direct, keys, net) -> None:
    net.reply(
        200,
        {
            "model": "gemini-3-flash-preview-001",
            "choices": [{"message": {"role": "assistant", "content": "SELECT 1 FROM t"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    )
    with fr.journal() as stamps:
        out = fr.complete("t", MSGS, pin=ROUTES["google"][0])
    assert out.ok is True and out.text == "SELECT 1 FROM t"
    stamp = out.stamp
    assert stamp.impl == "env-direct"
    assert stamp.line().startswith(PREFIX + ": FreeRoute t: asked google:gemini-3-flash-preview")
    assert fr.last_line(stamps).startswith(PREFIX)
    assert stamp.requested == "google:gemini-3-flash-preview"
    assert stamp.served == "google:gemini-3-flash-preview-001"
    assert stamp.served_provider == "google"
    assert stamp.served_model == "gemini-3-flash-preview-001"
    assert stamp.served_local is False
    rows = fr.scoreboard("t")
    assert [(r["requested"], r["served"]) for r in rows] == [
        ("google:gemini-3-flash-preview", "google:gemini-3-flash-preview-001")
    ]


def test_served_model_defaults_to_the_requested_one_and_is_honoured(direct, keys, net) -> None:
    net.reply(200, {"choices": [{"message": {"content": "SELECT 1 FROM t"}}]})  # no "model"
    out = fr.complete("t", MSGS, pin=ROUTES["nvidia"][0])
    assert out.ok is True
    assert out.stamp.served == "nvidia:moonshotai/kimi-k3"
    assert out.stamp.served_model == "moonshotai/kimi-k3"
    assert out.stamp.honored is True


def test_refusal_stamp_is_labelled_too(direct, keys, net) -> None:
    net.reply(429, {"message": "Requests rate limit exceeded", "type": "rate_limited"})
    out = fr.complete("t", MSGS, pin=ROUTES["mistral"][0])
    assert out.stamp.line().startswith(PREFIX + ": FreeRoute t: asked mistral:mistral-medium-latest")
    assert out.stamp.served_provider is None and out.stamp.served_local is False


# -- 6. provider 401 never poisons the OpenVault credential ------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_provider_auth_refusal_does_not_touch_openvault_credential_state(
    monkeypatch, direct, keys, net, vault, status
) -> None:
    noted: list[tuple[Any, ...]] = []
    real_note = fr.note_rejected
    monkeypatch.setattr(fr, "note_rejected", lambda *a, **k: (noted.append(a), real_note(*a, **k)))
    net.reply(status, {"status": status, "title": "Unauthorized", "detail": "Authentication failed"})
    out = fr.complete("t", MSGS, pin=ROUTES["nvidia"][0])
    assert out.ok is False
    assert out.reason == f"env-direct provider answered HTTP {status}: Authentication failed"
    assert noted == []
    assert fr._rejected == {}
    # Switch the opt-in off: OpenVault arming must not inherit a provider refusal.
    # The opt-in is latched per process, so a restart (reset) is what re-reads it.
    monkeypatch.delenv(direct_providers.TRANSPORT_ENV)
    direct_providers.reset_opt_in()
    fp = fr.fingerprint(OV_TOKEN)
    assert fr._rejection(openvault_client.openvault_base_url(), fp) == ""
    arm = fr.arming(fresh=True)
    assert arm.reason == "OpenVault unreachable at http://127.0.0.1:5000"
    assert ("GET", "/api/freeroute/status") in vault.calls


# -- 7. non-200 reasons are named ------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "status", "raw", "needle"),
    [
        (  # Mistral: reason at top level
            "mistral",
            429,
            b'{"object":"error","message":"Requests rate limit exceeded","type":"rate_limited","code":"1300"}',
            "Requests rate limit exceeded",
        ),
        (  # Google: error wrapped in a one-element list
            "google",
            400,
            b'[{"error":{"code":400,"message":"API key not valid. Please pass a valid API key.","status":"INVALID_ARGUMENT"}}]',
            "API key not valid. Please pass a valid API key.",
        ),
        (  # OpenAI shape
            "cerebras",
            402,
            b'{"error":{"message":"Payment required for this model","type":"payment_required"}}',
            "Payment required for this model",
        ),
        (  # problem+json (NVIDIA)
            "nvidia",
            500,
            b'{"status":500,"title":"Internal Server Error","detail":"upstream model crashed"}',
            "upstream model crashed",
        ),
    ],
)
def test_non_200_reason_is_named(direct, keys, net, label, status, raw, needle) -> None:
    net.reply(status, raw=raw)
    out = fr.complete("t", MSGS, pin=ROUTES[label][0])
    assert out.ok is False
    assert out.reason == f"env-direct provider answered HTTP {status}: {needle}"
    assert out.stamp.status == status and out.stamp.error == out.reason
    net.reply(status, raw=raw)
    code, body = direct_providers.request_json(
        "POST", "/v1/chat/completions", body={"model": ROUTES[label][0]}
    )
    assert code == status
    assert body is not None and body["error"]["message"] == needle
    assert len(net.requests) == 2


def test_unparseable_error_body_and_unreachable_host_are_still_named(direct, keys, net) -> None:
    net.reply(502, raw=b"<html>bad gateway</html>")
    out = fr.complete("t", MSGS, pin=ROUTES["google"][0])
    assert out.ok is False and out.reason == "env-direct provider answered HTTP 502"
    net.fail(urllib.error.URLError("no route"))
    out = fr.complete("t", MSGS, pin=ROUTES["google"][0])
    assert out.ok is False
    assert out.reason == "env-direct provider answered HTTP 0: google unreachable: URLError"


# -- 8. no in-call fallback ------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 402, 500])
def test_pinned_provider_refusal_makes_exactly_one_request(direct, keys, net, status) -> None:
    net.reply(status, {"message": "nope"})
    out = fr.complete("t", MSGS, pin=ROUTES["mistral"][0])
    assert out.ok is False
    assert net.urls == [ROUTES["mistral"][1]]
    assert out.stamp.requested == ROUTES["mistral"][0] and out.stamp.served == ""


def test_picked_provider_refusal_makes_exactly_one_request(direct, keys, net) -> None:
    net.reply(429, {"message": "slow down"})
    out = fr.complete("t", MSGS)  # no pin: the picker chooses; the call does not re-route
    assert out.ok is False
    assert len(net.requests) == 1
    assert out.stamp.requested == ROUTES["google"][0]
    assert net.urls == [ROUTES["google"][1]]


# -- 9. bad model ids are refused before any request --------------------------------


@pytest.mark.parametrize(
    "model",
    ["gemini-3-flash-preview", "openai:gpt-5", "google:", ":gemini-3-flash-preview", "auto", ""],
)
def test_bad_model_id_is_a_named_400_with_no_request(direct, keys, net, model) -> None:
    status, body = direct_providers.request_json(
        "POST", "/v1/chat/completions", body={"model": model}
    )
    assert status == 400
    assert body is not None
    assert "model id must be <provider>:<model>" in body["error"]["message"]
    assert "google, nvidia, mistral, cerebras" in body["error"]["message"]
    if model:
        out = fr.complete("t", MSGS, pin=model)
        assert out.ok is False
        assert out.stamp.status == 400
        assert "env-direct provider answered HTTP 400: env-direct model id must be" in out.reason
    assert net.requests == []


@pytest.mark.parametrize(("method", "path"), [("GET", "/v1/chat/completions"), ("POST", "/v1/models")])
def test_only_chat_completions_is_served(direct, keys, net, method, path) -> None:
    status, body = direct_providers.request_json(method, path, body={"model": ROUTES["google"][0]})
    assert status == 400 and body is not None
    assert "serves POST /v1/chat/completions only" in body["error"]["message"]
    assert net.requests == []


def test_known_provider_without_its_key_is_named_and_not_sent(monkeypatch, direct, net) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEYS["GEMINI_API_KEY"])
    noted: list[Any] = []
    monkeypatch.setattr(fr, "note_rejected", lambda *a, **k: noted.append(a))
    out = fr.complete("t", MSGS, pin=ROUTES["mistral"][0])
    assert out.ok is False
    assert out.reason == (
        "env-direct provider answered HTTP 401: env-direct: no key in env for mistral (MISTRAL_API_KEY)"
    )
    assert net.requests == [] and noted == []


# -- 10. leave gate ------------------------------------------------------------------


def test_leave_gate_allows_only_under_the_opt_in(monkeypatch, keys, net, gate) -> None:
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")
    allowed, why = fr.leave_gate()
    assert allowed is True
    assert "CORTEX_MODEL_TRANSPORT=env-direct" in why and "not consulted" in why
    assert gate == []
    out = fr.complete("t", MSGS, egress="leave", pin=ROUTES["google"][0])
    assert out.ok is True and len(net.requests) == 1 and gate == []

    for value in (None, "1", "direct"):
        if value is None:
            monkeypatch.delenv(direct_providers.TRANSPORT_ENV)
        else:
            monkeypatch.setenv(direct_providers.TRANSPORT_ENV, value)
        direct_providers.reset_opt_in()  # the opt-in is latched per process
        before = len(gate)
        allowed, why = fr.leave_gate()
        assert allowed is False and why == "vault is sealed"
        assert len(gate) == before + 1
        assert gate[-1]["action"] == "leave" and gate[-1]["destination"] == "freeroute"
    assert len(net.requests) == 1


# -- found while writing these: fixes in freeroute.py ---------------------------------


def test_operator_kill_switch_wins_over_the_opt_in(monkeypatch, direct, keys, net, vault) -> None:
    """``CORTEX_FREEROUTE=0`` is the model-layer kill switch; env-direct must obey it.

    The suite's hermetic fixture relies on it: with ``CORTEX_MODEL_TRANSPORT``
    exported in a developer shell next to real keys, every test that reaches
    ``complete()`` would otherwise spend a real provider key.
    """
    monkeypatch.setenv(fr.SWITCH_ENV, "0")
    arm = fr.arming()
    assert arm.armed is False
    assert arm.reason == "CORTEX_FREEROUTE=0: FreeRoute disabled by the operator (no fallback)"
    assert arm.public()["custody"] == direct_providers.CUSTODY
    out = fr.complete("t", MSGS, pin=ROUTES["google"][0])
    assert out.ok is False and "CORTEX_FREEROUTE=0" in out.reason
    assert net.requests == [] and vault.calls == []


def test_peek_reports_env_direct_custody(direct, keys, vault) -> None:
    """Crew /health shows ``peek()``; under the opt-in it must not claim OpenVault custody."""
    assert fr.arming().armed is True
    got = fr.peek()
    assert got.armed is True
    assert got.public()["custody"] == direct_providers.CUSTODY
    assert got.reason.startswith("armed env-direct (NOT OpenVault)")
    assert vault.calls == []


def test_local_only_refusal_stamp_is_labelled_env_direct(monkeypatch, direct, keys, net) -> None:
    monkeypatch.setenv(fr.LOCAL_ONLY_ENV, "1")
    with fr.journal() as stamps:
        out = fr.complete("t", MSGS, pin=ROUTES["google"][0])
    assert out.ok is False and net.requests == []  # fail-closed: nothing leaves
    assert out.stamp.impl == "env-direct"
    assert out.stamp.line().startswith(PREFIX + ": FreeRoute t: not sent (CORTEX_FREEROUTE_LOCAL_ONLY=1")
    assert fr.last_line(stamps).startswith(PREFIX)
