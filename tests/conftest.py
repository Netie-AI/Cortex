import importlib.util
import socket
import sys
import types
from pathlib import Path

import pytest


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
    "CORTEX_FREEROUTE_LOCAL_ONLY",
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


# Cortex #272 comment 5829081052: these three must run in push lint-type-test
# against the stubbed OpenVault. No skip marker, no xfail. If they do not
# pass, the session is Formal NOT GREEN.
_CORTEX_272_LINT_REQUIRED = (
    "test_local_only_drops_cloud_served_answer_text",
    "test_local_spendable_hop_arms_without_pooled_cloud_keys",
    "test_served_fields_come_from_response_not_requested_model",
    "test_local_only_unavailable_local_unreachable",
    "test_local_only_unavailable_local_model_not_loaded",
    "test_local_only_unavailable_local_base_url_not_loopback",
    "test_local_only_sealed_is_403_openvault_vault_sealed",
    "test_served_local_and_local_only_require_json_boolean_true",
)


def pytest_sessionfinish(session, exitstatus):
    """Print the #272 gate trio by name so a -q CI log still proves they passed."""
    items = list(getattr(session, "items", []) or [])
    if not any("test_freeroute_local.py" in (getattr(i, "nodeid", "") or "") for i in items):
        return
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    if reporter is None:
        return

    def _ids(key: str) -> list[str]:
        return [str(getattr(rep, "nodeid", "") or "") for rep in reporter.stats.get(key, [])]

    passed, skipped, xfailed, failed = (
        _ids("passed"),
        _ids("skipped"),
        _ids("xfailed"),
        _ids("failed"),
    )
    missing: list[str] = []
    reporter.write_sep("-", "CORTEX-272 LOCAL-1 lint-type-test proof")
    for short in _CORTEX_272_LINT_REQUIRED:
        hit_pass = next((n for n in passed if n.endswith(short) or f"::{short}" in n), "")
        if hit_pass and not any(short in n for n in skipped + xfailed + failed):
            reporter.write_line(f"CORTEX-272 lint-type-test PASSED {hit_pass}")
            continue
        state = (
            "skipped"
            if any(short in n for n in skipped)
            else "xfailed"
            if any(short in n for n in xfailed)
            else "failed"
            if any(short in n for n in failed)
            else "not-passed"
        )
        reporter.write_line(f"CORTEX-272 lint-type-test {state.upper()} {short}")
        missing.append(short)
    if missing:
        session.exitstatus = 1
