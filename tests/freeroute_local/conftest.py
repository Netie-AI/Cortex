"""#272 LOCAL-1 fixtures and lint-type-test proof printer.

Does not edit tests/conftest.py. Installs LocalFakeOpenVault as the transport
for this folder only.
"""

from __future__ import annotations

import socket

import pytest

from tests.freeroute_local_fake import LocalFakeOpenVault

# Cortex #272 comment 5829081052 plus OV#71 / Insights wiring. These must run
# in push lint-type-test against the stubbed OpenVault. No skip, no xfail.
_CORTEX_272_LINT_REQUIRED = (
    "test_local_only_drops_cloud_served_answer_text",
    "test_local_spendable_hop_arms_without_pooled_cloud_keys",
    "test_served_fields_come_from_response_not_requested_model",
    "test_local_only_unavailable_local_unreachable",
    "test_local_only_unavailable_local_model_not_loaded",
    "test_local_only_unavailable_local_base_url_not_loopback",
    "test_local_only_sealed_is_403_openvault_vault_sealed",
    "test_served_local_and_local_only_require_json_boolean_true",
    "test_router_fingerprint_equals_route_stamp",
    "test_router_fingerprint_empty_stamp_stays_null_false_plus_reason",
)


@pytest.fixture()
def fake_openvault(monkeypatch):
    """Scripted local OpenVault. Replaces the root FakeOpenVault for this folder."""
    from CortexOS.integrations import openvault_client

    fake = LocalFakeOpenVault()
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


def pytest_sessionfinish(session, exitstatus):
    """Print the #272 named cases so a -q CI log still proves they passed."""
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
