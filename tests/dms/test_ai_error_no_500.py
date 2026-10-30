"""AI-ERROR-NO-500-01: a failed model step is a named abstain on every ask exit.

``POST /dms/query`` already turns an engine or model-step exception into a named
abstain (DECISION5-LEGACY-01). ``POST /v1/contract/ask`` (engine path) and the
MCP ``answer_engine.answer`` tool called the same engine without that catch, so
an OpenVault call that raised inside the model step came back as a 500 with no
envelope. Each case drives the real DMS L2 port and asserts what the caller
receives (CLAUDE.md s8): status, badge, rows, sql_used and the rendered answer.
The success path (model SQL served, rows equal to running ``sql_used``) is
asserted unchanged on both exits.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.dms.test_c7_02_manifest_before_explain import _badge, dms_http  # noqa: F401
from tests.dms.test_decision5_no_rule_after_model_failure import (  # noqa: F401
    GOOD_SQL,
    LATE_Q,
    LEGACY_TEXT,
    _l2_real_port,
    _lake_rows,
    _multiset,
    legacy_spy,
)
from tests.dms.test_l2_freeroute_envelope import _reply_with_served_stamp

SESSION = "ai-error-sess"
GRANT = {"shipments": "TRUE"}
RAISED = "read timed out"
PROVIDER = "groq"
MODEL = "openai/gpt-oss-120b"


def _boom(prompt: str) -> str | None:
    raise TimeoutError(RAISED)


def _engine_down(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("engine exploded")


def _safe(client: TestClient) -> TestClient:
    """Same app, but a server exception comes back as a 500 instead of raising."""
    return TestClient(
        client.app, raise_server_exceptions=False, headers={"X-API-Key": "pytest-admin-key"}
    )


def _assert_named_abstain(body: dict[str, Any], *needles: str) -> None:
    assert _badge(body) == "abstain", body
    assert body.get("rows") in ([], None), body.get("rows")
    assert body.get("sql_used") is None, body.get("sql_used")
    text = body.get("answer") or ""
    assert "no rule-based answer was substituted" in text, text
    assert LEGACY_TEXT not in text, text
    # The exception message is not echoed (it can carry engine internals).
    assert RAISED not in text and "engine exploded" not in text, text
    for needle in needles:
        assert needle in text, text


def _contract_ask(client: TestClient) -> Any:
    return _safe(client).post(
        "/v1/contract/ask", json={"question": LATE_Q, "session_id": SESSION}
    )


def _mcp_ask(client: TestClient) -> Any:
    return _safe(client).post(
        "/mcp/call",
        json={
            "name": "answer_engine.answer",
            "arguments": {"question": LATE_Q, "session_id": SESSION},
        },
    )


# -- /v1/contract/ask ---------------------------------------------------------


def test_contract_ask_model_step_raising_is_a_named_abstain(
    armed_openvault, monkeypatch, dms_http, legacy_spy  # noqa: F811
) -> None:
    monkeypatch.setattr("packs.dms.generative.sql_generator._freeroute_complete", _boom)
    dms_http.bind_session(SESSION, GRANT)

    resp = _contract_ask(dms_http)

    assert resp.status_code == 200, resp.text
    _assert_named_abstain(resp.json(), "the model step failed (TimeoutError)")
    assert legacy_spy == []


def test_contract_ask_engine_error_is_a_named_abstain(
    monkeypatch, dms_http, legacy_spy  # noqa: F811
) -> None:
    monkeypatch.setattr("CortexOS.dms.answer_engine.answer", _engine_down)
    dms_http.bind_session(SESSION, GRANT)

    resp = _contract_ask(dms_http)

    assert resp.status_code == 200, resp.text
    _assert_named_abstain(resp.json(), "the answer engine failed (RuntimeError)")
    assert legacy_spy == []


def test_contract_ask_model_success_path_unchanged(armed_openvault, dms_http) -> None:  # noqa: F811
    _reply_with_served_stamp(armed_openvault, GOOD_SQL, provider=PROVIDER, model=MODEL)
    dms_http.bind_session(SESSION, GRANT)
    oracle = _lake_rows(GOOD_SQL)
    assert oracle, "fixture lake must hold delayed shipments"

    resp = _contract_ask(dms_http)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provenance"]["layer"] == "generated", body
    assert _multiset(body["rows"]) == _multiset(oracle)
    assert _multiset(_lake_rows(body["sql_used"])) == _multiset(oracle)
    for row in oracle:
        assert str(row["shipment_id"]) in body["answer"], body["answer"]


# -- MCP answer_engine.answer -------------------------------------------------


def test_mcp_answer_model_step_raising_is_a_named_abstain(
    armed_openvault, monkeypatch, dms_http, legacy_spy  # noqa: F811
) -> None:
    monkeypatch.setattr("packs.dms.generative.sql_generator._freeroute_complete", _boom)
    dms_http.bind_session(SESSION, GRANT)

    resp = _mcp_ask(dms_http)

    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["ok"] is True and out["name"] == "answer_engine.answer"
    _assert_named_abstain(out["result"], "the model step failed (TimeoutError)")
    assert legacy_spy == []


def test_mcp_answer_engine_error_is_a_named_abstain(
    monkeypatch, dms_http, legacy_spy  # noqa: F811
) -> None:
    monkeypatch.setattr("CortexOS.dms.answer_engine.answer", _engine_down)
    dms_http.bind_session(SESSION, GRANT)

    resp = _mcp_ask(dms_http)

    assert resp.status_code == 200, resp.text
    _assert_named_abstain(resp.json()["result"], "the answer engine failed (RuntimeError)")
    assert legacy_spy == []


def test_mcp_answer_model_success_path_unchanged(armed_openvault, dms_http) -> None:  # noqa: F811
    _reply_with_served_stamp(armed_openvault, GOOD_SQL, provider=PROVIDER, model=MODEL)
    dms_http.bind_session(SESSION, GRANT)
    oracle = _lake_rows(GOOD_SQL)

    resp = _mcp_ask(dms_http)

    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["badge"] != "abstain", result
    assert _multiset(result["rows"]) == _multiset(oracle)
    assert _multiset(_lake_rows(result["sql_used"])) == _multiset(oracle)
    for row in oracle:
        assert str(row["shipment_id"]) in result["answer"], result["answer"]


@pytest.mark.parametrize("ask", [_contract_ask, _mcp_ask], ids=["contract", "mcp"])
def test_manifest_refusal_is_not_swallowed_into_an_abstain(
    ask, monkeypatch, dms_http  # noqa: F811
) -> None:
    """A security refusal keeps its own exit; only engine failures become the abstain."""
    from CortexOS.execution.manifest import ManifestMalformed

    def _refuse(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ManifestMalformed("manifest unusable")

    monkeypatch.setattr("CortexOS.dms.answer_engine.answer", _refuse)
    dms_http.bind_session(SESSION, GRANT)

    resp = ask(dms_http)

    assert resp.status_code != 200 or "no rule-based answer" not in resp.text, resp.text
