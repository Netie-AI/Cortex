"""T2-FAILCLOSED (#263): API auth is fail-closed by default.

Founder decision 2026-09-25 (#263 comments 5825984024 and 5825990095):

* With ``DMS_API_KEYS`` unset and no OpenVault authorizer armed, every gated
  request is refused: no valid key gives 401, no registered authorizer gives
  503. There is no built-in or demo key, admin included.
* No shipped image sets ``DMS_AUTH_DISABLED``. The bypass is an explicit
  local-dev opt-in and is logged at WARNING when an app is built with it.
* The formerly published demo key values are gone from the setup script and the
  secrets templates. The local demo generates random per-install keys instead.
* The operator desk shell loads in a browser without a key and carries no data;
  every data call it makes still needs the operator's key.
* A caller with a valid key and a sufficient role sees no change.

Every assertion is on something a user or operator sees: HTTP status and body,
the log record, or the contents of a shipped file.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.security import auth_port

ROOT = Path(__file__).resolve().parents[2]

# The values that used to be built in and published. Spelled from parts so a
# plain grep for a whole value finds only the files that must not carry it.
_ROLES = ("viewer", "steward", "admin")
PUBLISHED_DEMO_KEYS = tuple(f"dms-demo-{role}-key" for role in _ROLES)

KEYS = {"viewer": "t2fc-viewer-key", "steward": "t2fc-steward-key", "admin": "t2fc-admin-key"}
_KEY_ENV = ";".join(f"{role}:{key}" for role, key in KEYS.items())

# Gated reads: one through the engine auth port, one through the pack's own
# dependency, so both ways a route is gated are covered.
_PORT_GATED = "/api/connectors/agents"
_PACK_GATED = "/api/engine/specs"
_DESK = "/api/connectors"
_DESK_DATA = (
    "/api/connectors/agents",
    "/api/connectors/agents/constructor/messages",
    "/api/connectors/computer-control",
    "/api/connectors/workspaces",
    "/api/connectors/cursor/chats",
)


def _client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, auth_disabled: bool = False
) -> TestClient:
    monkeypatch.setenv("PACK", "dms")
    if auth_disabled:
        monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    else:
        monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL", raising=False)
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL_EXECUTE", raising=False)
    from CortexOS.connectors import agents, cursor_session
    from packs.dms.security import rate_limit

    rate_limit.reset_limiter(1_000_000)
    cursor_session.reset_for_tests(tmp_path / "chats.json")
    agents.reset_for_tests()
    # No OpenVault: any ov_ token the pack would forward is unverifiable.
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_client.post_json", lambda *a, **k: None
    )
    import packs.dms.security.api_auth  # noqa: F401 - registers the DMS authorizer
    from CortexOS.api.app import create_app

    return TestClient(create_app())


@pytest.fixture
def no_keys(monkeypatch, tmp_path):
    """``DMS_API_KEYS`` unset, the DMS authorizer registered, OpenVault unreachable."""
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    monkeypatch.delenv("DMS_REFUSE_DEMO_KEYS", raising=False)
    client = _client(monkeypatch, tmp_path)
    yield client
    from CortexOS.connectors import agents, cursor_session
    from packs.dms.security import rate_limit

    rate_limit.reset_limiter()
    cursor_session.reset_for_tests()
    agents.reset_for_tests()


@pytest.fixture
def keyed(monkeypatch, tmp_path):
    monkeypatch.setenv("DMS_API_KEYS", _KEY_ENV)
    client = _client(monkeypatch, tmp_path)
    yield client
    from CortexOS.connectors import agents, cursor_session
    from packs.dms.security import rate_limit

    rate_limit.reset_limiter()
    cursor_session.reset_for_tests()
    agents.reset_for_tests()


def _hdr(key: str | None) -> dict[str, str]:
    return {"X-API-Key": key} if key else {}


# --- (a) no shipped Dockerfile turns auth off -------------------------------

_TRUTHY = r"""["']?\s*(?:1|true|yes|on)\s*["']?(?:\s|$)"""
_DISABLED_SET = re.compile(
    r"\bDMS_AUTH_DISABLED\b\s*(?:=|\s)\s*" + _TRUTHY, re.IGNORECASE
)


def _dockerfiles() -> list[Path]:
    skip = {".git", "node_modules", ".venv", "myenv", ".claude"}
    return sorted(
        p
        for p in ROOT.rglob("Dockerfile*")
        if p.is_file() and not (skip & set(p.relative_to(ROOT).parts))
    )


def _instructions(text: str) -> list[str]:
    """Dockerfile instructions with comments dropped and ``\\`` continuations joined."""
    out: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        if buf.strip():
            out.append(buf.strip())
        buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def test_no_dockerfile_sets_auth_disabled_to_a_truthy_value() -> None:
    files = _dockerfiles()
    names = {p.name for p in files}
    assert {"Dockerfile", "Dockerfile.core", "Dockerfile.full", "Dockerfile.constructor"} <= names
    offenders = [
        f"{p.relative_to(ROOT)}: {ins}"
        for p in files
        for ins in _instructions(p.read_text(encoding="utf-8"))
        if _DISABLED_SET.search(ins)
    ]
    assert offenders == []


def test_dockerfile_check_catches_both_env_forms() -> None:
    """The guard above cannot pass vacuously: it sees each way ENV can set it."""
    for text in (
        "ENV PACK=dms \\\n    DMS_AUTH_DISABLED=1\n",
        "ENV DMS_AUTH_DISABLED true\n",
        'ENV A=1 DMS_AUTH_DISABLED="yes"\n',
    ):
        assert any(_DISABLED_SET.search(i) for i in _instructions(text)), text
    for text in ("# DMS_AUTH_DISABLED=1\n", "ENV DMS_AUTH_DISABLED=0\n", "ENV PACK=dms\n"):
        assert not any(_DISABLED_SET.search(i) for i in _instructions(text)), text


# --- (b) with no keys configured, nothing built in is accepted ----------------


def test_no_keys_configured_refuses_demo_and_builtin_keys(no_keys, monkeypatch) -> None:
    client = no_keys
    from packs.dms.security.api_auth import parse_api_keys, resolve_caller

    # Nothing is configured, so nothing is a key.
    assert parse_api_keys() == {}
    candidates = [*PUBLISHED_DEMO_KEYS, "admin", "demo", "ov_demo"]
    for key in candidates:
        assert resolve_caller(key) is None, key
    for path in (_PORT_GATED, _PACK_GATED):
        res = client.get(path)
        assert res.status_code == 401, (path, res.status_code, res.text)
        for key in candidates:
            res = client.get(path, headers=_hdr(key))
            assert res.status_code == 401, (path, key, res.status_code, res.text)
            assert "Valid API key required" in res.text

    # No authorizer armed at all: the engine-gated route refuses with 503, with
    # or without a key, and never falls through to the handler.
    auth_port.clear_authorizer()
    monkeypatch.setattr(auth_port, "_load_active_pack", lambda: None)
    try:
        for key in (None, *PUBLISHED_DEMO_KEYS):
            res = client.get(_PORT_GATED, headers=_hdr(key))
            assert res.status_code == 503, (key, res.status_code, res.text)
            assert res.json() == {"detail": auth_port.NO_AUTHORIZER_DETAIL}
    finally:
        from packs.dms.security.api_auth import register_request_authorizer

        register_request_authorizer()


def test_refuse_demo_keys_variable_is_accepted_and_changes_nothing(no_keys, monkeypatch) -> None:
    """``DMS_REFUSE_DEMO_KEYS`` is kept for old deploy configs; it is a no-op now."""
    for value in ("1", "0", ""):
        monkeypatch.setenv("DMS_REFUSE_DEMO_KEYS", value)
        for key in PUBLISHED_DEMO_KEYS:
            assert no_keys.get(_PACK_GATED, headers=_hdr(key)).status_code == 401


# --- (c) the published demo values are gone from shipped files ----------------


def _shipped_key_files() -> list[Path]:
    files = [ROOT / "SETUP_ONCE.ps1", *sorted((ROOT / "secrets").glob("*.example*"))]
    assert len(files) >= 2 and all(p.is_file() for p in files), files
    return files


def test_published_demo_keys_are_not_in_setup_or_secret_templates() -> None:
    found = [
        f"{p.relative_to(ROOT)} carries {key}"
        for p in _shipped_key_files()
        for key in PUBLISHED_DEMO_KEYS
        if key in p.read_text(encoding="utf-8-sig")
    ]
    assert found == []


_DEMO_LAUNCHERS = (
    ROOT / "SETUP_ONCE.ps1",
    ROOT / "demo" / "run_demo.ps1",
    ROOT / "scripts" / "start_cortex_engine.ps1",
)
_KEY_HELPER = ROOT / "scripts" / "demo_keys.ps1"
_KEY_FILE_REL = "data/local/demo_api_keys.env"


def test_local_demo_generates_random_per_install_keys() -> None:
    """Static check (no PowerShell in CI): every launcher gets its keys from the
    generator, none assigns a literal key, and the generator uses a CSPRNG."""
    helper = _KEY_HELPER.read_text(encoding="utf-8")
    assert "System.Security.Cryptography.RandomNumberGenerator" in helper
    assert "function Initialize-DemoApiKeys" in helper
    assert "data\\local\\demo_api_keys.env" in helper
    for name in ("NEXT_PUBLIC_DMS_VIEWER_KEY", "NEXT_PUBLIC_DMS_STEWARD_KEY", "NEXT_PUBLIC_DMS_ADMIN_KEY"):
        assert name in helper
    assert "$env:DMS_API_KEYS" in helper
    literal_assignment = re.compile(r"""\$env:DMS_API_KEYS\s*=\s*["'][^"'$]*:""")
    for script in _DEMO_LAUNCHERS:
        text = script.read_text(encoding="utf-8-sig")
        assert 'scripts\\demo_keys.ps1"' in text, script.name
        assert "Initialize-DemoApiKeys -Root $Root" in text, script.name
        assert not literal_assignment.search(text), script.name
        for key in PUBLISHED_DEMO_KEYS:
            assert key not in text, (script.name, key)


def test_local_demo_key_file_is_gitignored() -> None:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("git checkout not available")
    res = subprocess.run(
        [git, "check-ignore", "-q", _KEY_FILE_REL],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr


def test_demo_ui_reads_keys_from_env_and_says_so_when_unset() -> None:
    ui = ROOT / "demo" / "dms-ui"
    api = (ui / "lib" / "api.js").read_text(encoding="utf-8")
    for name in ("NEXT_PUBLIC_DMS_VIEWER_KEY", "NEXT_PUBLIC_DMS_STEWARD_KEY", "NEXT_PUBLIC_DMS_ADMIN_KEY"):
        assert f"process.env.{name}" in api
    assert "No Cortex API key configured for role" in api
    shell = (ui / "components" / "AppShell.jsx").read_text(encoding="utf-8")
    assert "missingApiKeyMessage" in shell
    for rel in ("lib/api.js", "playwright.config.js", "e2e/reliability.spec.js"):
        text = (ui / rel).read_text(encoding="utf-8")
        for key in PUBLISHED_DEMO_KEYS:
            assert key not in text, (rel, key)
    config = (ui / "playwright.config.js").read_text(encoding="utf-8")
    assert "process.env.NEXT_PUBLIC_DMS_VIEWER_KEY" in config
    assert '"X-API-Key": VIEWER_KEY' in config


# --- operator desk: static shell, gated data ----------------------------------


def _roster_strings() -> list[str]:
    from CortexOS.connectors import agents

    out: list[str] = []
    for a in agents.roster():
        for field in ("snippet", "blurb", "role"):
            value = str(a.get(field) or "")
            if len(value) >= 12:
                out.append(value)
    return out


def test_desk_shell_loads_without_a_key_and_carries_no_data(no_keys, tmp_path) -> None:
    res = no_keys.get(_DESK)
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert res.headers.get("cache-control") == "no-store"
    html = res.text
    assert "Constructor Agent" in html and "Message Constructor Agent" in html
    # No engine data: no roster text, no probe result, no host or tmp path.
    for text in _roster_strings():
        assert text not in html, text
    assert "uacc_importable" not in html.split("<script>", 1)[0]
    assert str(tmp_path) not in html and str(ROOT) not in html
    assert not re.search(r"[A-Za-z]:\\\\|/home/|/Users/|/tmp/", html)
    # The page authenticates its own calls, keeps the key per tab, never in a URL.
    assert "'X-API-Key': key" in html
    assert "sessionStorage" in html and "localStorage" not in html
    assert not re.search(r"[?&](api_?key|key|token)=", html, re.IGNORECASE)
    # The same bytes for everyone: nothing in it depends on who asked.
    for key in (KEYS["admin"], *PUBLISHED_DEMO_KEYS):
        assert no_keys.get(_DESK, headers=_hdr(key)).text == html


def test_desk_data_calls_still_refuse_without_a_valid_key(no_keys) -> None:
    for path in _DESK_DATA:
        for key in (None, *PUBLISHED_DEMO_KEYS):
            res = no_keys.get(path, headers=_hdr(key))
            assert res.status_code == 401, (path, key, res.status_code)
    res = no_keys.post(
        "/api/connectors/agents/constructor/messages", json={"text": "x", "kind": "task"}
    )
    assert res.status_code == 401


# --- (d) no false positive ----------------------------------------------------


def _ungated(monkeypatch: pytest.MonkeyPatch, client: TestClient, path: str) -> Any:
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    try:
        return client.get(path)
    finally:
        monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)


def test_valid_key_with_sufficient_role_sees_unchanged_response(keyed, monkeypatch) -> None:
    client = keyed
    # An authenticated operator opens the desk (a browser sends no header on
    # navigation), then every data call it makes with the key answers exactly as
    # the ungated route does.
    shell = client.get(_DESK)
    assert shell.status_code == 200 and "Constructor Agent" in shell.text
    for path in (*_DESK_DATA, _PACK_GATED):
        want = _ungated(monkeypatch, client, path)
        assert want.status_code == 200, path
        for role in _ROLES:
            got = client.get(path, headers=_hdr(KEYS[role]))
            assert got.status_code == 200, (path, role, got.text)
            assert got.json() == want.json(), (path, role)
    posted = client.post(
        "/api/connectors/agents/constructor/messages",
        json={"text": "t2fc desk check", "kind": "task"},
        headers=_hdr(KEYS["steward"]),
    )
    assert posted.status_code == 200
    msgs = client.get(
        "/api/connectors/agents/constructor/messages", headers=_hdr(KEYS["viewer"])
    ).json()["messages"]
    assert any(m.get("text") == "t2fc desk check" for m in msgs)
    # Below the role is still 403, and a configured install still ignores the
    # formerly published values.
    assert client.post(
        "/api/connectors/dispatch", json={"text": "x", "kind": "task"}, headers=_hdr(KEYS["viewer"])
    ).status_code == 403
    for key in PUBLISHED_DEMO_KEYS:
        assert client.get(_PACK_GATED, headers=_hdr(key)).status_code == 401


# --- the local-dev bypass is loud -----------------------------------------------


def test_auth_bypass_logs_a_warning_when_the_app_is_built(monkeypatch, tmp_path, caplog) -> None:
    monkeypatch.setenv("DMS_API_KEYS", _KEY_ENV)
    with caplog.at_level(logging.WARNING, logger="CortexOS.security.auth"):
        _client(monkeypatch, tmp_path, auth_disabled=True)
    hits = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "DMS_AUTH_DISABLED is set" in r.getMessage()
    ]
    assert hits, [r.getMessage() for r in caplog.records]
    assert "authentication is OFF" in hits[0].getMessage()


def test_no_bypass_warning_on_a_normal_keyed_install(monkeypatch, tmp_path, caplog) -> None:
    monkeypatch.setenv("DMS_API_KEYS", _KEY_ENV)
    with caplog.at_level(logging.WARNING, logger="CortexOS.security.auth"):
        _client(monkeypatch, tmp_path)
    assert not [r for r in caplog.records if r.name == "CortexOS.security.auth"]


# --- the suite's test-key plugin does not override a test's own choice ------------


def test_suite_plugin_supplies_test_keys_by_default() -> None:
    import os

    from tests.api_key_isolation import TEST_API_KEYS

    assert os.environ.get("DMS_API_KEYS") == TEST_API_KEYS


def test_suite_plugin_yields_to_a_test_that_deletes_the_keys(monkeypatch) -> None:
    import os

    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    assert "DMS_API_KEYS" not in os.environ


@pytest.fixture
def own_keys(monkeypatch):
    monkeypatch.setenv("DMS_API_KEYS", "admin:own-fixture-key")


def test_suite_plugin_yields_to_a_test_fixture_that_sets_keys(own_keys) -> None:
    import os

    assert os.environ.get("DMS_API_KEYS") == "admin:own-fixture-key"


# --- the desk in a real browser ------------------------------------------------


def _browser_gate_unavailable(reason: str) -> None:
    """Skip locally, but fail where CI declares the browser gate required."""
    import os

    if os.environ.get("CORTEX_BROWSER_GATE") == "required":
        pytest.fail(f"CORTEX_BROWSER_GATE=required but {reason}")
    pytest.skip(reason)


def test_desk_in_a_browser_asks_once_and_sends_the_key_on_every_call(monkeypatch, tmp_path) -> None:
    import socket
    import threading
    import time

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        _browser_gate_unavailable("python-playwright not installed: desk browser check NOT run")
    uvicorn = pytest.importorskip("uvicorn")

    monkeypatch.setenv("DMS_API_KEYS", _KEY_ENV)
    app = _client(monkeypatch, tmp_path).app
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        assert time.time() < deadline and thread.is_alive(), "uvicorn did not start"
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as pw:
            try:
                chromium = pw.chromium.launch()
            except PlaywrightError as exc:  # pragma: no cover - environment dependent
                _browser_gate_unavailable(f"Chromium could not launch: desk browser check NOT run: {exc}")
            try:
                page = chromium.new_context().new_page()
                prompts: list[str] = []
                calls: list[tuple[str, str | None]] = []

                def _answer(dialog: Any) -> None:
                    prompts.append(dialog.type)
                    dialog.accept(KEYS["viewer"])

                page.on("dialog", _answer)
                page.on(
                    "request",
                    lambda req: calls.append((req.url, req.headers.get("x-api-key")))
                    if "/api/connectors/" in req.url
                    else None,
                )
                page.goto(base + _DESK)
                page.wait_for_selector(".agent.on", timeout=15_000)
                page.wait_for_function(
                    "document.getElementById('ccchip').textContent !== 'computer control status loading'"
                )
                assert prompts == ["prompt"]
                assert page.evaluate("sessionStorage.getItem('cortex_operator_key')") == KEYS["viewer"]
                assert page.locator(".agent").count() >= 1
                assert page.locator("#authbar").is_hidden()
                data_calls = [c for c in calls if "/api/connectors/" in c[0]]
                assert data_calls and all(key == KEYS["viewer"] for _, key in data_calls), data_calls
                assert all(KEYS["viewer"] not in url for url, _ in calls)

                # A refused key is dropped and the operator is told, not shown data.
                page.evaluate("sessionStorage.setItem('cortex_operator_key', 'not-a-key')")
                page.remove_listener("dialog", _answer)
                page.on("dialog", lambda d: d.dismiss())
                page.reload()
                page.wait_for_selector("#authbar:not([style*='display: none'])", timeout=15_000)
                assert "401" in page.locator("#authbar").inner_text()
                assert page.locator(".agent").count() == 0
                assert page.evaluate("sessionStorage.getItem('cortex_operator_key')") is None
            finally:
                chromium.close()
    finally:
        server.should_exit = True
        thread.join(10)
        from CortexOS.connectors import agents, cursor_session
        from packs.dms.security import rate_limit

        rate_limit.reset_limiter()
        cursor_session.reset_for_tests()
        agents.reset_for_tests()


# --- coordinator fix on verify round 1: copied placeholders and published values never authenticate ---


@pytest.mark.parametrize(
    "configured",
    [
        "viewer:REPLACE_WITH_RANDOM_VIEWER_KEY;steward:REPLACE_WITH_RANDOM_STEWARD_KEY;admin:REPLACE_WITH_RANDOM_ADMIN_KEY",
        "viewer:dms-demo-viewer-key;steward:dms-demo-steward-key;admin:dms-demo-admin-key",
        "admin:replace_with_anything",
    ],
)
def test_template_placeholders_and_published_values_never_authenticate(configured):
    """A deploy that copies secrets/dms.env.example.yaml unchanged, or pastes the
    old published demo values, must not get a working key."""
    from packs.dms.security.api_auth import parse_api_keys

    assert parse_api_keys(configured) == {}


def test_example_template_keys_parse_to_nothing():
    import re
    from pathlib import Path

    from packs.dms.security.api_auth import parse_api_keys

    text = (Path(__file__).resolve().parents[2] / "secrets" / "dms.env.example.yaml").read_text(encoding="utf-8")
    m = re.search(r'^DMS_API_KEYS:\s*"([^"]*)"', text, re.M)
    assert m, "DMS_API_KEYS line missing from the template"
    assert parse_api_keys(m.group(1)) == {}


def test_real_keys_next_to_a_placeholder_still_work():
    from packs.dms.security.api_auth import parse_api_keys

    got = parse_api_keys("viewer:REPLACE_WITH_RANDOM_VIEWER_KEY;admin:9f3c1e7a-real-admin")
    assert list(got) == ["9f3c1e7a-real-admin"] and got["9f3c1e7a-real-admin"].role == "admin"
