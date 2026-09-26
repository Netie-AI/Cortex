"""HX-01: one secret list, scrubbed children, _FILE custody, env-direct opt-in (PRD R1.4-R1.6, R5.4).

Stubbed only: dummy keys, a fake transport, captured subprocess calls. No network.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from typing import Any

import pytest

from CortexOS.integrations import direct_providers
from CortexOS.integrations import freeroute as fr
from CortexOS.integrations.harness import registry
from CortexOS.integrations.harness import secrets as harness_secrets

MSGS = [{"role": "user", "content": "total units"}]


def _dummy(name: str) -> str:
    return f"dummy-{name.lower()}-9f3k2q7x"


@pytest.fixture(autouse=True)
def hermetic(freeroute_hermetic, monkeypatch, tmp_path):
    for name in (
        *harness_secrets.secret_env_names(),
        direct_providers.TRANSPORT_ENV,
        "CORTEX_DIRECT_PROVIDERS",
        harness_secrets.KEEP_PROVIDER_KEYS_ENV,
        fr.SWITCH_ENV,
        fr.MODELS_ENV,
        fr.LOCAL_ONLY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    for p in getattr(direct_providers, "CATALOGUE", direct_providers.PROVIDERS):
        monkeypatch.delenv(p.models_env, raising=False)
    monkeypatch.setenv(fr.STORE_ENV, str(tmp_path / "hx01_routes.db"))
    harness_secrets.clear_file_cache()
    fr.reset()
    yield
    harness_secrets.clear_file_cache()
    fr.reset()


class _Resp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture()
def net(monkeypatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def fake(req: Any, timeout: float | None = None) -> _Resp:
        body = json.loads(req.data.decode("utf-8"))
        sent.append({"url": req.full_url, "auth": req.get_header("Authorization"), "body": body})
        reply = {"model": body.get("model"), "choices": [{"message": {"role": "assistant", "content": "SELECT 1"}}]}
        return _Resp(200, json.dumps(reply).encode("utf-8"))

    monkeypatch.setattr(direct_providers, "_urlopen", fake)
    return sent


@pytest.fixture()
def planted(monkeypatch) -> dict[str, str]:
    """Every secret env name the registry knows, set to a dummy. At least 20."""
    names = [n for n in harness_secrets.secret_env_names() if not n.endswith(harness_secrets.FILE_SUFFIX)]
    values = {n: _dummy(n) for n in names}
    for n, v in values.items():
        monkeypatch.setenv(n, v)
    for n in names[:3]:
        monkeypatch.setenv(n + harness_secrets.FILE_SUFFIX, f"/run/secrets/{n.lower()}")
        values[n + harness_secrets.FILE_SUFFIX] = f"/run/secrets/{n.lower()}"
    monkeypatch.setenv("GH_TOKEN", "gh-token-kept-for-gh")
    monkeypatch.setenv("PATH", "/usr/bin")
    assert len(values) >= 20
    return values


def _assert_clean(env: dict[str, str], planted: dict[str, str]) -> None:
    leaked = sorted(set(env) & set(planted))
    assert leaked == [], f"secret env names reached a child: {leaked}"
    blob = json.dumps(env)
    assert not [v for v in planted.values() if v in blob]


# -- the one list ---------------------------------------------------------------


def test_secret_env_names_cover_every_row_file_form_and_extras() -> None:
    names = set(harness_secrets.secret_env_names())
    for env in registry.all_key_envs():
        assert env in names and env + "_FILE" in names
    for extra in ("CORTEX_FREEROUTE_TOKEN", "AWS_BEARER_TOKEN_BEDROCK", "GMAIL_APP_PASSWORD"):
        assert extra in names and extra + "_FILE" in names
    for alias in ("NVIDIA_NIM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"):
        assert alias in names
    assert "GH_TOKEN" not in names
    assert len(names) >= 40


# -- R1.4: no child inherits a secret -------------------------------------------


def test_child_env_strips_every_planted_secret(planted) -> None:
    out = fr.child_env()
    _assert_clean(out, planted)
    assert out["PATH"] == "/usr/bin"


def test_crew_laptop_shell_child_gets_a_scrubbed_env(monkeypatch, planted) -> None:
    from CortexOS.crew import shell

    seen: list[dict[str, str]] = []

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append(kw.get("env"))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(shell.subprocess, "run", run)
    result = shell.LaptopAdapter().exec(["gh", "pr", "list"])
    assert result.ok is True
    assert len(seen) == 1 and seen[0] is not None, "laptop child inherited the full host env"
    _assert_clean(seen[0], planted)
    assert seen[0]["GH_TOKEN"] == "gh-token-kept-for-gh"


def test_crew_shell_keep_keys_opt_out_is_laptop_only(monkeypatch, planted) -> None:
    from CortexOS.crew import shell

    monkeypatch.setenv(harness_secrets.KEEP_PROVIDER_KEYS_ENV, "1")
    assert shell.laptop_env()["OPENAI_API_KEY"] == planted["OPENAI_API_KEY"]
    _assert_clean(shell.isolate_env(None, forward_host=True), planted)


def test_crew_isolate_forwarding_strips_file_forms(planted) -> None:
    from CortexOS.crew import shell

    out = shell.isolate_env(
        {"GEMINI_API_KEY_FILE": "/run/secrets/g", "CI": "1"}, forward_host=True
    )
    _assert_clean(out, planted)
    assert "GEMINI_API_KEY_FILE" not in out
    assert out["CI"] == "1"


def test_gh_child_keeps_gh_token_and_nothing_else_secret(monkeypatch, planted) -> None:
    from CortexOS.crew import github

    seen: list[dict[str, str] | None] = []

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append(kw.get("env"))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(github.subprocess, "run", run)
    github._run(["gh", "auth", "status"])
    assert len(seen) == 1 and seen[0] is not None, "gh inherited the full host env"
    _assert_clean(seen[0], planted)
    assert seen[0]["GH_TOKEN"] == "gh-token-kept-for-gh"


# -- R1.6: _FILE custody, read per call, never written to os.environ -----------


def test_key_file_custody_arms_and_sends_without_touching_environ(monkeypatch, tmp_path, net) -> None:
    key_file = tmp_path / "gemini.key"
    key_file.write_text("gm-file-key-111111\n", encoding="utf-8")
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")
    monkeypatch.setenv("GEMINI_API_KEY_FILE", str(key_file))
    before = dict(os.environ)

    arm = fr.arming()
    assert arm.armed is True
    assert arm.reason == "armed env-direct (NOT OpenVault): google via GEMINI_API_KEY_FILE"
    assert "gm-file-key" not in json.dumps(arm.public())
    status, _ = direct_providers.request_json(
        "POST", "/v1/chat/completions", body={"model": "google:gemini-3-flash-preview", "messages": MSGS}
    )
    assert status == 200
    assert net[0]["auth"] == "Bearer gm-file-key-111111"
    assert net[0]["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert dict(os.environ) == before


def test_key_file_rotates_without_restart_after_the_cache_window(monkeypatch, tmp_path) -> None:
    clock = [1000.0]
    monkeypatch.setattr(harness_secrets, "_now", lambda: clock[0])
    key_file = tmp_path / "k"
    key_file.write_text("first-key-aaaaaa", encoding="utf-8")
    env = {"MISTRAL_API_KEY_FILE": str(key_file)}
    assert harness_secrets.read_key(("MISTRAL_API_KEY",), env) == ("first-key-aaaaaa", "MISTRAL_API_KEY_FILE")
    key_file.write_text("second-key-bbbbbb", encoding="utf-8")
    clock[0] += harness_secrets.FILE_CACHE_S - 0.1
    assert harness_secrets.read_key(("MISTRAL_API_KEY",), env)[0] == "first-key-aaaaaa"
    clock[0] += 0.2
    assert harness_secrets.read_key(("MISTRAL_API_KEY",), env)[0] == "second-key-bbbbbb"


def test_env_value_wins_over_file_and_missing_file_is_unset(tmp_path) -> None:
    env = {"CEREBRAS_API_KEY": "cb-env", "CEREBRAS_API_KEY_FILE": str(tmp_path / "nope")}
    assert harness_secrets.read_key(("CEREBRAS_API_KEY",), env) == ("cb-env", "CEREBRAS_API_KEY")
    assert harness_secrets.read_key(("CEREBRAS_API_KEY",), {"CEREBRAS_API_KEY_FILE": str(tmp_path / "nope")}) == ("", "")


# -- R1.5: new rows need an explicit opt-in ---------------------------------------


def test_openai_key_for_crew_does_not_arm_env_direct_openai(monkeypatch, net) -> None:
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-crew-only-key-123456")
    monkeypatch.setenv("CORTEX_DIRECT_OPENAI_MODELS", "gpt-test")
    assert [p.label for p, _, _ in direct_providers.configured()] == []
    assert fr.arming().armed is False
    status, body = direct_providers.request_json(
        "POST", "/v1/chat/completions", body={"model": "openai:gpt-test", "messages": MSGS}
    )
    assert status == 400 and net == []
    assert "google, nvidia, mistral, cerebras" in body["error"]["message"]


def test_opt_in_serves_openai_after_the_defaults(monkeypatch, net) -> None:
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")
    monkeypatch.setenv(direct_providers.OPT_IN_ENV, "openai, anthropic, nosuch")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-opted-in-key-123456")
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-default-key-123456")
    monkeypatch.setenv("CORTEX_DIRECT_OPENAI_MODELS", "gpt-test")
    assert [p.label for p in direct_providers.served_providers()] == [
        "google",
        "nvidia",
        "mistral",
        "cerebras",
        "openai",
    ]
    arm = fr.arming()
    assert arm.reason == "armed env-direct (NOT OpenVault): nvidia via NVIDIA_API_KEY, openai via OPENAI_API_KEY"
    status, _ = direct_providers.request_json(
        "POST", "/v1/chat/completions", body={"model": "openai:gpt-test", "messages": MSGS}
    )
    assert status == 200
    assert net[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert net[0]["auth"] == "Bearer sk-opted-in-key-123456"
    assert net[0]["body"]["model"] == "gpt-test"


def test_provider_opt_in_latches_at_first_read(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-latched-key-123456")
    assert "openai" not in [p.label for p in direct_providers.served_providers()]
    monkeypatch.setenv(direct_providers.OPT_IN_ENV, "openai")
    assert "openai" not in [p.label for p in direct_providers.served_providers()]
    direct_providers.reset_opt_in()
    assert "openai" in [p.label for p in direct_providers.served_providers()]


# -- R5.4: error text is redacted ------------------------------------------------


def test_redact_secrets_removes_live_values_key_shapes_and_pii(monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "plainvalue-without-shape")
    text = (
        "401 for key plainvalue-without-shape; Authorization: Bearer abc.def; "
        "nvapi-12ab sk-ant-api03-zzzzzzzzzz AIzaSyXXXX; owner ali@example.com IC 900101-14-5678"
    )
    out = harness_secrets.redact_secrets(text)
    for leak in ("plainvalue", "abc.def", "nvapi-12ab", "sk-ant", "AIzaSy", "ali@example.com", "900101-14-5678"):
        assert leak not in out, leak
    assert out.startswith("401 for key <redacted>")


def test_redact_secrets_fails_closed_when_masking_breaks(monkeypatch) -> None:
    def boom(_text: str) -> str:
        raise RuntimeError("detector down")

    monkeypatch.setattr(harness_secrets, "_mask_pii", boom)
    assert harness_secrets.redact_secrets("anything at all") == harness_secrets.REDACTED
