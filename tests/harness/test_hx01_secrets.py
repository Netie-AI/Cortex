"""HX-01: one secret list, scrubbed children, _FILE custody, redaction (PRD R1.4, R1.6, R5.4).

Stubbed only: dummy keys, captured subprocess calls. No network.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import subprocess
from typing import Any

import pytest

from CortexOS.integrations import freeroute as fr
from CortexOS.integrations.harness import ov_key_envs
from CortexOS.integrations.harness import secrets as harness_secrets


def _dummy(name: str) -> str:
    return f"dummy-{name.lower()}-9f3k2q7x"


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    for name in (*harness_secrets.secret_env_names(), harness_secrets.KEEP_PROVIDER_KEYS_ENV):
        monkeypatch.delenv(name, raising=False)
    harness_secrets.clear_file_cache()
    yield
    harness_secrets.clear_file_cache()


@pytest.fixture()
def planted(monkeypatch) -> dict[str, str]:
    """Every secret env name, set to a dummy, plus three ``_FILE`` forms."""
    names = [n for n in harness_secrets.secret_env_names() if not n.endswith(harness_secrets.FILE_SUFFIX)]
    values = {n: _dummy(n) for n in names}
    for n, v in values.items():
        monkeypatch.setenv(n, v)
    for n in names[:3]:
        monkeypatch.setenv(n + harness_secrets.FILE_SUFFIX, f"/run/secrets/{n.lower()}")
        values[n + harness_secrets.FILE_SUFFIX] = f"/run/secrets/{n.lower()}"
    monkeypatch.setenv("GH_TOKEN", "gh-token-kept-for-gh")
    monkeypatch.setenv("PATH", "/usr/bin")
    assert len(values) >= 30
    return values


def _assert_clean(env: dict[str, str], planted: dict[str, str]) -> None:
    leaked = sorted(set(env) & set(planted))
    assert leaked == [], f"secret env names reached a child: {leaked}"
    blob = json.dumps(env)
    assert not [v for v in planted.values() if v in blob]


# -- the one list ---------------------------------------------------------------


def test_secret_env_names_cover_the_ov_mirror_file_forms_and_extras() -> None:
    names = set(harness_secrets.secret_env_names())
    for env in ov_key_envs.OV_KEY_ENVS:
        if env in harness_secrets.NOT_SCRUBBED:
            continue
        assert env in names and env + "_FILE" in names
    for extra in harness_secrets.EXTRA_SECRET_ENVS:
        assert extra in names and extra + "_FILE" in names
    for name in ("CORTEX_FREEROUTE_TOKEN", "NVIDIA_NIM_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "HF_TOKEN"):
        assert name in names
    assert "GH_TOKEN" not in names and "GITHUB_TOKEN" not in names
    assert len(names) == len(harness_secrets.secret_env_names())


# -- R1.4: no child inherits a secret -------------------------------------------


def test_child_env_strips_every_planted_secret(planted) -> None:
    out = fr.child_env()
    _assert_clean(out, planted)
    assert out["PATH"] == "/usr/bin"
    assert out["GH_TOKEN"] == "gh-token-kept-for-gh"


def test_mcp_child_env_strips_every_planted_secret(monkeypatch, planted) -> None:
    from CortexOS.crew import mcp_client

    spec = mcp_client.MCPServerSpec(name="probe", command=["python", "-c", "pass"], armed=True)
    seen: dict[str, Any] = {}

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs.get("env") or {})
        raise RuntimeError("stop before spawning")

    monkeypatch.setattr(mcp_client.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError):
        asyncio.run(mcp_client.MCPClient(spec).start())
    assert seen, "spawn env was not captured"
    _assert_clean(seen, planted)


def test_app_runner_child_env_strips_every_planted_secret(monkeypatch, tmp_path, planted) -> None:
    from CortexOS.execution import app_runner

    seen: list[dict[str, str]] = []

    def popen(argv: list[str], **kw: Any) -> Any:
        seen.append(kw["env"])
        raise OSError("stop before spawning")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(app_runner.subprocess, "Popen", popen)
    out = app_runner.start(
        app_id="hx01", stack="python", cwd=tmp_path, port=port, commands={"start": ["python", "-V"]}
    )
    assert out["ok"] is False and out["error"].startswith("start_spawn:")
    assert len(seen) == 1
    _assert_clean(seen[0], planted)
    assert seen[0]["PORT"] == str(port)


def test_crew_laptop_shell_child_gets_a_scrubbed_env(monkeypatch, planted) -> None:
    from CortexOS.crew import shell

    seen: list[dict[str, str] | None] = []

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
    _assert_clean(fr.child_env(), planted)


def test_crew_isolate_forwarding_strips_file_forms(planted) -> None:
    from CortexOS.crew import shell

    out = shell.isolate_env({"GEMINI_API_KEY_FILE": "/run/secrets/g", "CI": "1"}, forward_host=True)
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


def test_freeroute_token_file_is_presented_without_touching_environ(monkeypatch, tmp_path) -> None:
    token_file = tmp_path / "freeroute.token"
    token_file.write_text("ov_file_custody_token_1234\n", encoding="utf-8")
    monkeypatch.setenv(fr.TOKEN_ENV + "_FILE", str(token_file))
    before = dict(os.environ)

    assert fr.auth_headers() == {"Authorization": "Bearer ov_file_custody_token_1234"}
    assert fr.identity()["mode"] == "api_key"
    assert "ov_file_custody_token" not in json.dumps(fr.identity())
    child = fr.child_env()
    assert fr.TOKEN_ENV not in child and fr.TOKEN_ENV + "_FILE" not in child
    assert dict(os.environ) == before


def test_key_file_rotates_without_restart_after_the_cache_window(monkeypatch, tmp_path) -> None:
    clock = [1000.0]
    monkeypatch.setattr(harness_secrets, "_now", lambda: clock[0])
    key_file = tmp_path / "k"
    key_file.write_text("first-key-aaaaaa", encoding="utf-8")
    env = {"CORTEX_FREEROUTE_TOKEN_FILE": str(key_file)}
    names = ("CORTEX_FREEROUTE_TOKEN",)
    assert harness_secrets.read_key(names, env) == ("first-key-aaaaaa", "CORTEX_FREEROUTE_TOKEN_FILE")
    key_file.write_text("second-key-bbbbbb", encoding="utf-8")
    clock[0] += harness_secrets.FILE_CACHE_S - 0.1
    assert harness_secrets.read_key(names, env)[0] == "first-key-aaaaaa"
    clock[0] += 0.2
    assert harness_secrets.read_key(names, env)[0] == "second-key-bbbbbb"


def test_env_value_wins_over_file_and_missing_file_is_unset(tmp_path) -> None:
    missing = str(tmp_path / "nope")
    env = {"CORTEX_FREEROUTE_TOKEN": "ov_env", "CORTEX_FREEROUTE_TOKEN_FILE": missing}
    assert harness_secrets.read_key(("CORTEX_FREEROUTE_TOKEN",), env) == ("ov_env", "CORTEX_FREEROUTE_TOKEN")
    assert harness_secrets.read_key(("CORTEX_FREEROUTE_TOKEN",), {"CORTEX_FREEROUTE_TOKEN_FILE": missing}) == ("", "")


def test_oversized_key_file_is_unset(tmp_path) -> None:
    big = tmp_path / "big"
    big.write_bytes(b"x" * (harness_secrets.MAX_KEY_FILE_BYTES + 1))
    assert harness_secrets.read_key(("CORTEX_FREEROUTE_TOKEN",), {"CORTEX_FREEROUTE_TOKEN_FILE": str(big)}) == ("", "")


# -- R5.4: error text is redacted ------------------------------------------------


def test_redact_secrets_removes_live_values_and_key_shapes(monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "plainvalue-without-shape")
    text = (
        "401 for key plainvalue-without-shape; Authorization: Bearer abc.def; "
        "nvapi-12ab " + "sk-" + "ant-api03-zzzzzzzzzz AIzaSyXXXX ov_relay_token hf_abcdef"
    )
    out = harness_secrets.redact_secrets(text)
    for leak in ("plainvalue", "abc.def", "nvapi-12ab", "sk-ant", "AIzaSy", "ov_relay", "hf_abc"):
        assert leak not in out, leak
    assert out.startswith("401 for key <redacted>")


def test_crew_vault_error_detail_never_echoes_the_key(monkeypatch, tmp_path) -> None:
    from CortexOS.crew import keys, openvault

    secret = "hx01-dummy-must-never-leave-123456"
    monkeypatch.delenv("CREW_VAULT_LAST_ERROR", raising=False)
    monkeypatch.setattr(openvault, "upsert_env_key", lambda k, s: {"ok": False, "detail": f"HTTP 400: bad {s}"})
    try:
        keys.save(tmp_path, {"OPENAI_API_KEY": secret})
        err = os.environ["CREW_VAULT_LAST_ERROR"]
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("CREW_VAULT_LAST_ERROR", None)
    assert secret not in err
    assert err == "HTTP 400: bad <redacted>"


# -- R5.5: keys.json is 0600 from the first byte ---------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
@pytest.mark.parametrize("existing", [False, True])
def test_keys_json_is_0600_whether_new_or_existing_0644(monkeypatch, tmp_path, existing) -> None:
    from CortexOS.crew import keys

    path = tmp_path / "keys.json"
    if existing:
        path.write_text("{}", encoding="utf-8")
        os.chmod(path, 0o644)
    opened: list[int] = []
    real_open = os.open

    def spy(p: Any, flags: int, mode: int = 0o777, *a: Any, **k: Any) -> int:
        opened.append(mode)
        return real_open(p, flags, mode, *a, **k)

    monkeypatch.delenv("CREW_MODEL", raising=False)
    monkeypatch.setattr(keys.os, "open", spy)
    old = os.umask(0o022)
    try:
        keys.save(tmp_path, {"CREW_MODEL": "x/y"})
    finally:
        os.umask(old)
        os.environ.pop("CREW_MODEL", None)
    assert opened == [0o600], "keys.json was not created 0600 from the first byte"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not (tmp_path / "keys.json.tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"CREW_MODEL": "x/y"}
