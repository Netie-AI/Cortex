"""Cortex#6 / SEC-01 — POST /dms/query must enforce the session manifest.

Acceptance is on the customer envelope (badge / violations_blocked / rows /
answer), not on the generated SQL string.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from CortexOS.execution.session_manifests import reset_session_registry_for_tests
from tests.dms.session_manifest import bind_warehouse_session

IN_MANIFEST_QUESTION = "How many SKUs do we have in inventory?"
OUT_OF_MANIFEST_QUESTION = "Which suppliers have a risk score above 0.7?"


@pytest.fixture
def dms_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    import netie.config

    netie.config._cached_config = None
    from CortexOS.api.app import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _load_warehouse() -> None:
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()


def _post_query(client: TestClient, question: str, session_id: str) -> dict:
    res = client.post("/dms/query", json={"question": question, "session_id": session_id})
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, dict)
    return body


@pytest.mark.no_auto_manifest
def test_unbound_session_refuses_on_envelope(dms_client: TestClient) -> None:
    reset_session_registry_for_tests()
    env = _post_query(dms_client, IN_MANIFEST_QUESTION, "nobody")
    assert env["badge"] == "blocked"
    assert env["route"] == "blocked"
    assert "SessionUnbound" in env["violations_blocked"]
    assert env["sql_used"] is None
    assert not env.get("rows")
    assert env.get("audit_id")
    assert "manifest" in (env.get("answer") or "").lower()


def test_in_manifest_question_still_answers(dms_client: TestClient) -> None:
    reset_session_registry_for_tests()
    bind_warehouse_session("full-sess")
    env = _post_query(dms_client, IN_MANIFEST_QUESTION, "full-sess")
    assert env["route"] == "sql"
    assert env["badge"] == "certified"
    assert env["violations_blocked"] == []
    assert env.get("rows")
    assert env.get("sql_used")
    assert env.get("audit_id")
    answer = (env.get("answer") or "").lower()
    assert answer
    assert "can't answer" not in answer
    assert any(ch.isdigit() for ch in answer)


def test_out_of_manifest_table_is_path_not_allowed(dms_client: TestClient) -> None:
    reset_session_registry_for_tests()
    bind_warehouse_session("inv-only", tables=("inventory",))
    env = _post_query(dms_client, OUT_OF_MANIFEST_QUESTION, "inv-only")
    assert env["badge"] == "blocked"
    assert env["route"] == "blocked"
    assert "PathNotAllowed" in env["violations_blocked"]
    assert env["sql_used"] is None
    assert not env.get("rows")
    assert env.get("audit_id")


def test_statement_not_allowed_on_envelope(
    dms_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.dms.answer_engine import MetricPlan
    from CortexOS.dms.sql_validate_gate import ValidateGateResult

    reset_session_registry_for_tests()
    bind_warehouse_session("stmt-sess")
    monkeypatch.setattr("CortexOS.dms.answer_engine.match_certified", lambda _q: None)
    monkeypatch.setattr(
        "CortexOS.dms.answer_engine.route_to_metric",
        lambda _q: MetricPlan("sku_count", {}, "injected"),
    )
    monkeypatch.setattr(
        "packs.dms.semantic.loader.compile_metric",
        lambda *_a, **_k: "INSERT INTO inventory (sku) VALUES ('x')",
    )
    monkeypatch.setattr(
        "CortexOS.dms.sql_validate_gate.run_gate",
        lambda sql, semantic, con=None: ValidateGateResult(
            passed=True, safe_sql=sql, explain_ok=True, attempts=1
        ),
    )
    env = _post_query(dms_client, IN_MANIFEST_QUESTION, "stmt-sess")
    assert env["badge"] == "blocked"
    assert "StatementNotAllowed" in env["violations_blocked"]
    assert env["sql_used"] is None
    assert not env.get("rows")


def test_sql_not_analyzable_on_envelope(
    dms_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.dms.answer_engine import MetricPlan

    reset_session_registry_for_tests()
    bind_warehouse_session("analyzable-sess")
    monkeypatch.setattr("CortexOS.dms.answer_engine.match_certified", lambda _q: None)
    monkeypatch.setattr(
        "CortexOS.dms.answer_engine.route_to_metric",
        lambda _q: MetricPlan("sku_count", {}, "injected"),
    )
    monkeypatch.setattr(
        "packs.dms.semantic.loader.compile_metric",
        lambda *_a, **_k: "SELECT main.inventory.sku FROM inventory",
    )
    env = _post_query(dms_client, IN_MANIFEST_QUESTION, "analyzable-sess")
    assert env["badge"] == "blocked"
    assert "SqlNotAnalyzable" in env["violations_blocked"]
    assert env["sql_used"] is None
    assert not env.get("rows")


def test_answer_engine_has_no_ungoverned_execute() -> None:
    from CortexOS.dms import answer_engine, query_service

    assert "guard_and_execute" not in inspect.getsource(answer_engine.answer)
    assert "guard_and_execute" not in inspect.getsource(
        query_service._answer_question_legacy
    )
