import importlib.util
import os
import socket
import sys
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# H2-TEST-ISOLATION (#254): tests never write the runtime decision or shadow logs
#
# ``CortexOS.decision.decision_log`` and ``CortexOS.decision.shadow`` append to
# ``data/engine/tier_decisions.jsonl`` and ``data/engine/tier_shadow.jsonl`` by
# default. KEV-CALIB reads those files as real outcomes, so a test that runs a
# routed DAG without redirecting them poisons the calibration corpus with
# synthetic rows (138 of them after one full run, verified on 275fe13).
#
# The env names are spelled here rather than imported so that conftest does
# not import the engine at collection time; the meta test asserts they match
# ``decision_log.PATH_ENV`` and ``shadow.PATH_ENV``.
# ---------------------------------------------------------------------------

RUNTIME_LOG_ENV = {
    "CORTEX_DECISION_LOG_PATH": "tier_decisions.jsonl",
    "CORTEX_KEV_SHADOW_PATH": "tier_shadow.jsonl",
}
RUNTIME_LOG_GUARD_ENV = "CORTEX_RUNTIME_LOG_GUARD"
_RUNTIME_LOG_SNAPSHOT: pytest.StashKey[dict[str, tuple[int, int] | None]] = pytest.StashKey()


def _runtime_log_files() -> dict[str, Path]:
    """The real runtime files, resolved the way the engine resolves them (repo
    anchored, never cwd) but without the env override, so the snapshot is of the
    files the fixture is meant to protect even when a shell exports an override."""
    root = Path(__file__).resolve().parents[1]
    return {name: root / "data" / "engine" / name for name in RUNTIME_LOG_ENV.values()}


def snapshot_runtime_logs() -> dict[str, tuple[int, int] | None]:
    """``{filename: (size, mtime_ns)}``, or ``None`` when the file is absent."""
    out: dict[str, tuple[int, int] | None] = {}
    for name, path in _runtime_log_files().items():
        try:
            st = path.stat()
        except FileNotFoundError:
            out[name] = None
        else:
            out[name] = (st.st_size, st.st_mtime_ns)
    return out


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.stash[_RUNTIME_LOG_SNAPSHOT] = snapshot_runtime_logs()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """CI guard: the suite must leave the runtime logs byte-identical.

    Always reports a change in the terminal. Fails the session (exit 1) only when
    ``CORTEX_RUNTIME_LOG_GUARD=1``: on a laptop a live engine may legitimately
    append to these files while the suite runs, and a guard that fails a green
    run because of unrelated production traffic is itself a defect.
    """
    before = session.config.stash.get(_RUNTIME_LOG_SNAPSHOT, None)
    if before is None:
        return
    after = snapshot_runtime_logs()
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    if not changed:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    lines = [f"runtime log {k} changed during the test session: {b} -> {a}" for k, (b, a) in changed.items()]
    if reporter is not None:
        reporter.ensure_newline()
        reporter.section("H2-TEST-ISOLATION", sep="=", red=True, bold=True)
        for line in lines:
            reporter.line(line, red=True)
    else:
        for line in lines:
            sys.stderr.write(line + "\n")
    if os.environ.get(RUNTIME_LOG_GUARD_ENV, "").strip() == "1":
        session.exitstatus = max(int(exitstatus), 1)


@pytest.fixture(scope="session")
def runtime_log_guard(request: pytest.FixtureRequest) -> types.SimpleNamespace:
    """Handle on the guard for meta tests, handed out as a fixture because the
    loaded conftest module is not importable by a stable name (see
    ``_freeroute_fake``).

    ``baseline``: size and mtime of both runtime files (or None) at session start;
    ``snapshot()``: the same reading now; ``files``: the real paths; ``env``: the
    env name to filename map the autouse fixture sets.
    """
    return types.SimpleNamespace(
        baseline=dict(request.config.stash[_RUNTIME_LOG_SNAPSHOT]),
        snapshot=snapshot_runtime_logs,
        files=_runtime_log_files(),
        env=dict(RUNTIME_LOG_ENV),
        guard_env=RUNTIME_LOG_GUARD_ENV,
    )


@pytest.fixture(autouse=True)
def runtime_logs_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """Point the decision log and the shadow log at a per-test tmp path.

    Autouse fixtures run before a test's own fixtures and body, so a test that
    sets ``CORTEX_DECISION_LOG_PATH`` / ``CORTEX_KEV_SHADOW_PATH`` itself (via
    ``monkeypatch.setenv``) still wins; this only replaces the default, which
    would otherwise be the real ``data/engine/`` files.
    """
    paths: dict[str, Path] = {}
    for env_name, filename in RUNTIME_LOG_ENV.items():
        path = tmp_path / "runtime_logs" / filename
        monkeypatch.setenv(env_name, str(path))
        paths[env_name] = path
    return paths


def _freeroute_fake():
    """Load tests/freeroute_fake.py by path.

    ``import tests...`` can resolve to another checkout's ``tests`` package when an
    editable install puts that checkout on sys.path; a path load cannot.
    """
    name = "_cortex_freeroute_fake"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("freeroute_fake.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_FREEROUTE_ENV_DROP = (
    "CORTEX_FREEROUTE_TOKEN",
    "CORTEX_FREEROUTE_MODELS",
    "CORTEX_FREEROUTE_LEARN",
    "DMS_L2_MODEL",
    "OPENVAULT_SQL_MODEL",
    "CREW_OPENVAULT_MODEL",
)


@pytest.fixture(autouse=True)
def freeroute_hermetic(monkeypatch, tmp_path):
    """FreeRoute is off in every test unless a test arms the scripted OpenVault.

    A live, unsealed OpenVault runs on this laptop's :5000. Without this, any
    test that reaches the model layer would spend real keys and pass or fail on
    the founder's vault state instead of the code.
    """
    monkeypatch.setenv("CORTEX_FREEROUTE", "0")
    for name in _FREEROUTE_ENV_DROP:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CORTEX_FREEROUTE_SCOREBOARD", str(tmp_path / "freeroute_routes.db"))
    from CortexOS.integrations import freeroute

    freeroute.reset()
    yield
    freeroute.reset()


@pytest.fixture()
def fake_openvault(monkeypatch):
    """Scripted OpenVault installed as the transport. FreeRoute stays switched off."""
    from CortexOS.integrations import openvault_client

    fake = _freeroute_fake().FakeOpenVault()
    for name in ("OPENVAULT_BASE_URL", "OPENVAULT_URL", "CREW_OPENVAULT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(openvault_client, "request_json", fake)
    gate_calls: list[dict] = []
    fake.gate_allowed = True
    fake.gate_reasons = ["vault is sealed"]
    fake.gate_calls = gate_calls

    def _gate(**kwargs):
        gate_calls.append(kwargs)
        if fake.gate_allowed:
            return {"ok": True, "allowed": True, "reasons": []}
        return {"ok": True, "allowed": False, "reasons": list(fake.gate_reasons)}

    monkeypatch.setattr("CortexOS.integrations.openvault_gate.check_gate", _gate)

    real_connect = socket.socket.connect

    def _guarded(self, address):
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        if port in (5000, 11434):
            raise AssertionError(f"test reached a live service on port {port}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", _guarded)
    return fake


@pytest.fixture()
def armed_openvault(fake_openvault, monkeypatch):
    """FreeRoute on, pointed at the scripted OpenVault (unsealed, pooled, spendable)."""
    monkeypatch.delenv("CORTEX_FREEROUTE", raising=False)
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "0")
    from CortexOS.integrations import freeroute

    freeroute.reset()
    return fake_openvault


@pytest.fixture(autouse=True)
def reset_config_cache(monkeypatch):
    """Prevent config.toml / env from polluting pack path resolution in tests."""
    monkeypatch.setenv("PACK", "ruma")
    import netie.config

    netie.config._cached_config = None
    yield
    netie.config._cached_config = None


@pytest.fixture(autouse=True)
def mock_sentence_transformer(monkeypatch):
    """
    Mocks sentence_transformers before any importer loads the real stack
    (avoids heavyweight sklearn/scipy imports on constrained CI images).
    """
    class MockTransformer:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, sentences, *args, **kwargs):
            if isinstance(sentences, str):
                return [0.1] * 384
            return [[0.1] * 384 for _ in sentences]

    fake_st = types.ModuleType("sentence_transformers")
    fake_st.SentenceTransformer = MockTransformer
    fake_st.CrossEncoder = MockTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers.models",
        types.ModuleType("sentence_transformers.models"),
    )

    # skillmesh imports SentenceTransformer lazily (packaging profiles: the rag
    # extra may be absent), so the module-level symbol only exists on builds that
    # still bind it at import time. Where it is absent the sys.modules fake above
    # already covers the deferred import.
    for mod_name in ("netie.fabrication.skillmesh", "CortexOS.fabrication.skillmesh"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "SentenceTransformer"):
            monkeypatch.setattr(f"{mod_name}.SentenceTransformer", MockTransformer)

    return MockTransformer
