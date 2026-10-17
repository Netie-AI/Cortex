"""DECISION5-LEGACY-01: a failed model step never falls to a rule answer.

``answer_question`` (behind ``POST /dms/query`` and the in-process Insights
bridge) used to serve the pre-Q2 keyword SQL generator in two places:

* the engine raised (an OpenVault call that raised inside the model step, or
  any other engine error), and
* the engine abstained after the L2 model step failed (timeout, empty reply,
  plausibility block) and the question text matched a ranked template.

Both now end in a named abstain: the rendered answer says which step failed,
``rows`` is empty and ``sql_used`` is null. Every case drives the real DMS L2
port against the scripted OpenVault and asserts what the caller receives
(CLAUDE.md s8). The success path (model SQL served, rows equal to running
``sql_used`` on the test lake) is asserted unchanged.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import duckdb
import pytest

from CortexOS.dms import l2_generation
from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401

SESSION = "decision5-sess"
# Reaches L2 (no governed metric covers "last week by carrier") and its text
# matches the legacy delayed/late ranked template, so the old bridge served
# the generic delayed-shipment listing for it.
LATE_Q = "which shipments were late last week by carrier"
GOOD_SQL = (
    "SELECT shipment_id, carrier FROM shipments "
    "WHERE status = 'DELAYED' ORDER BY shipment_id LIMIT 5"
)
LEGACY_TEXT = "delayed shipment row(s)"


@pytest.fixture(autouse=True)
def _l2_real_port(monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.generative.l2_adapter import DmsL2Generation

    _ensure_db_loaded()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.delenv("DMS_L2_SHADOW", raising=False)
    port = DmsL2Generation()
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: port)


@pytest.fixture
def legacy_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from CortexOS.dms import query_service

    calls: list[str] = []
    real = query_service._answer_question_legacy

    def _spy(question: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(question)
        return real(question, **kwargs)

    monkeypatch.setattr(query_service, "_answer_question_legacy", _spy)
    return calls


def _chat_timeout(fake: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenVault chat times out: request_json reports (0, None), as on a real timeout."""
    from CortexOS.integrations import openvault_client

    def _transport(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        if path.split("?", 1)[0] == "/v1/chat/completions":
            fake.calls.append(
                {"method": method, "path": path, "body": dict(kwargs.get("body") or {}),
                 "headers": dict(kwargs.get("headers") or {}), "base": kwargs.get("base")}
            )
            return 0, None
        return fake(method, path, **kwargs)

    monkeypatch.setattr(openvault_client, "request_json", _transport)


def _chat_empty(fake: Any) -> None:
    for _ in range(6):
        fake.reply("")


def _lake_rows(sql: str) -> list[dict[str, Any]]:
    from CortexOS.dms.warehouse_db import DEFAULT_DB

    con = duckdb.connect(str(DEFAULT_DB), read_only=True)
    try:
        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def _multiset(rows: list[dict[str, Any]]) -> Counter[str]:
    return Counter(json.dumps(r, sort_keys=True, default=str) for r in rows)


def _assert_named_abstain(body: dict[str, Any], *needles: str) -> None:
    assert body.get("badge") == "abstain", body
    assert body.get("rows") in ([], None), body.get("rows")
    assert body.get("sql_used") is None, body.get("sql_used")
    text = body.get("answer") or ""
    assert LEGACY_TEXT not in text, text
    for needle in needles:
        assert needle in text, text


# -- /dms/query ---------------------------------------------------------------


def test_dms_query_model_timeout_abstains_without_legacy_rows(
    armed_openvault, monkeypatch, dms_http, legacy_spy  # noqa: F811
) -> None:
    _chat_timeout(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, {"shipments": "TRUE"})
    resp = dms_http.post("/dms/query", json={"question": LATE_Q, "session_id": SESSION})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_named_abstain(body, "L2 generation failed", "OpenVault unreachable")
    assert len(armed_openvault.chat_calls) >= 1
    assert legacy_spy == []


def test_dms_query_model_empty_reply_abstains_without_legacy_rows(
    armed_openvault, dms_http, legacy_spy  # noqa: F811
) -> None:
    _chat_empty(armed_openvault)
    dms_http.bind_session(SESSION, {"shipments": "TRUE"})
    resp = dms_http.post("/dms/query", json={"question": LATE_Q, "session_id": SESSION})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_named_abstain(body, "L2 generation failed", "answer unusable")
    assert len(armed_openvault.chat_calls) >= 1
    assert legacy_spy == []


def test_dms_query_model_step_raising_is_named_not_legacy(
    armed_openvault, monkeypatch, dms_http, legacy_spy  # noqa: F811
) -> None:
    def _boom(prompt: str) -> str | None:
        raise TimeoutError("read timed out")

    monkeypatch.setattr("packs.dms.generative.sql_generator._freeroute_complete", _boom)
    dms_http.bind_session(SESSION, {"shipments": "TRUE"})
    resp = dms_http.post("/dms/query", json={"question": LATE_Q, "session_id": SESSION})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_named_abstain(body, "model step failed (TimeoutError)")
    assert legacy_spy == []


def test_engine_error_is_named_not_legacy(monkeypatch, legacy_spy) -> None:
    from CortexOS.dms.query_service import answer_question

    def _engine_down(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("engine exploded")

    monkeypatch.setattr("CortexOS.dms.answer_engine.answer", _engine_down)
    body = answer_question("top 5 sales")
    _assert_named_abstain(body, "answer engine failed (RuntimeError)")
    assert body["route"] == "needs_clarification"
    assert body["layer"] == "abstain"
    # The exception message is not echoed (it can carry engine internals).
    assert "engine exploded" not in body["answer"]
    assert legacy_spy == []


def test_dms_query_model_success_path_unchanged(
    armed_openvault, dms_http, legacy_spy  # noqa: F811
) -> None:
    armed_openvault.reply(GOOD_SQL)
    dms_http.bind_session(SESSION, {"shipments": "TRUE"})
    resp = dms_http.post("/dms/query", json={"question": LATE_Q, "session_id": SESSION})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["layer"] == "generated", body
    assert body["badge"] == "L2_VALIDATED"
    rows = body.get("rows") or []
    assert len(rows) == 5
    assert _multiset(rows) == _multiset(_lake_rows(body["sql_used"]))
    text = body.get("answer") or ""
    for row in rows:
        assert str(row["shipment_id"]) in text
    assert legacy_spy == []


# -- Insights bridge (LocalEngineBridge, used by POST /v1/insights) ------------


def _bridge_ask(question: str) -> dict[str, Any]:
    import asyncio

    from CortexOS.crew.engine_bridge import LocalEngineBridge

    return asyncio.run(LocalEngineBridge(session_id=SESSION).ask(question))


def test_insights_bridge_model_timeout_abstains_without_legacy_rows(
    armed_openvault, monkeypatch, dms_http, legacy_spy  # noqa: F811
) -> None:
    _chat_timeout(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, {"shipments": "TRUE"})
    body = _bridge_ask(LATE_Q)
    assert body["ok"] is True
    _assert_named_abstain(body, "L2 generation failed", "OpenVault unreachable")
    assert len(armed_openvault.chat_calls) >= 1
    assert legacy_spy == []


def test_insights_bridge_engine_error_is_named_not_legacy(
    monkeypatch, dms_http, legacy_spy  # noqa: F811
) -> None:
    def _engine_down(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("engine exploded")

    monkeypatch.setattr("CortexOS.dms.answer_engine.answer", _engine_down)
    dms_http.bind_session(SESSION, {"shipments": "TRUE"})
    body = _bridge_ask(LATE_Q)
    _assert_named_abstain(body, "answer engine failed (RuntimeError)")
    assert legacy_spy == []


def test_insights_bridge_model_success_path_unchanged(
    armed_openvault, dms_http, legacy_spy  # noqa: F811
) -> None:
    armed_openvault.reply(GOOD_SQL)
    dms_http.bind_session(SESSION, {"shipments": "TRUE"})
    body = _bridge_ask(LATE_Q)
    assert body["badge"] == "L2_VALIDATED", body
    rows = body.get("rows") or []
    assert len(rows) == 5
    assert _multiset(rows) == _multiset(_lake_rows(body["sql_used"]))
    text = body.get("answer") or ""
    for row in rows:
        assert str(row["shipment_id"]) in text
    assert legacy_spy == []
