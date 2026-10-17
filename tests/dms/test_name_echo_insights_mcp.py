"""NAME-ECHO-INSIGHTS-01: the NAME-ECHO scrub also covers /v1/insights and the MCP answer tool.

NAME-ECHO-CX-01 withholds names outside the caller's grant at the exits of
``/v1/contract/ask`` and ``/v1/contract/drillthrough``. Before this item a
model's invented table or column still came back from ``POST /v1/insights``
(answer prose, ``refuse_reason``, ``generative.refuse_reason``, validation
notes, climb step reasons, ``served_reason``) and from the MCP tool
``answer_engine.answer`` (answer prose, assumptions). The same scrub
(:mod:`CortexOS.api.response_scrub`) now runs at both exits.

Assertions are on what the caller receives: the whole JSON body, the rendered
answer text and, for the MCP tool, the rows checked against an oracle run on
the test lake (CLAUDE.md section 8). The model is scripted.
"""

# ruff: noqa: F811  (pytest fixtures are imported, then requested by name)
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.api.response_scrub import WITHHELD
from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401
from tests.dms.test_l2_freeroute_envelope import QUESTION as MCP_QUESTION
from tests.dms.test_name_echo_scrub import _oracle, _rowset
from tests.dms.test_schema_context_01 import (
    INTENT as SCHEMA_INTENT,
)
from tests.dms.test_schema_context_01 import (
    SCHEMA,
    SQL_OK,
    _bind,
    _complete,
    _oracle_qty_over_one,
    _post,
    _test_lake,
)
from tests.dms.test_schema_context_01 import (
    api as insights_api,  # noqa: F401
)

# Names nothing in these tests declares or grants. Each is checked on the whole body.
VAULT = "zz_payroll_vault"
BAND = "zz_salary_band"
GHOST = "zz_ghost_col"
INVENTED = (VAULT, BAND, GHOST)
PACK_INTENT = "how many skus"


def _assert_no_invented(payload: Any, names: tuple[str, ...] = INVENTED) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    for name in names:
        assert name not in text, (name, text)
        assert name.upper() not in text, (name, text)


def _insights(
    api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sql: str,
    *,
    intent: str = PACK_INTENT,
    schema: str | None = None,
) -> dict[str, Any]:
    prompts: list[tuple[str, str]] = []
    _bind(monkeypatch, _complete(prompts, lambda _prompt: sql))
    payload: dict[str, Any] = {"intent": intent, "ask": False, "generate": True}
    if schema is not None:
        payload["schema_context"] = schema
    body = _post(api, payload)
    assert prompts, "the scripted model was never asked"
    return body


def _free_text(body: dict[str, Any]) -> list[str]:
    """The free-text fields the caller reads on an insights refusal."""
    gen = body.get("generative") or {}
    steps = ((gen.get("climb") or {}).get("steps")) or []
    validation = body.get("validation") or {}
    whys = [
        row.get("why")
        for key in ("include", "exclude", "unsure")
        for row in validation.get(key) or []
    ]
    return [
        str(text)
        for text in [
            body.get("answer"),
            body.get("refuse_reason"),
            body.get("served_reason"),
            gen.get("refuse_reason"),
            *[step.get("reason") for step in steps],
            *whys,
        ]
        if text
    ]


# -- POST /v1/insights -------------------------------------------------------------


def test_insights_pack_path_invented_table_is_withheld(insights_api, monkeypatch) -> None:
    body = _insights(insights_api, monkeypatch, f"SELECT {BAND} FROM {VAULT} WHERE 1 = 1")

    _assert_no_invented(body)
    assert body["status"] == "REFUSE", body
    assert body["values"] == []
    assert not body.get("sql_used")
    # The refusal is still named; only the identifier is withheld.
    reason = "sql reads tables outside ontology ranking: " + WITHHELD
    assert reason in body["answer"], body["answer"]
    assert reason in body["generative"]["refuse_reason"]
    assert reason in body["served_reason"]
    steps = body["generative"]["climb"]["steps"]
    assert [s["reason"] for s in steps if s["kind"] == "generate"]
    assert all(s["reason"] == reason for s in steps if s["kind"] == "generate")
    assert any(reason in row["why"] for row in body["validation"]["unsure"])


def test_insights_pack_path_invented_column_keeps_the_ranked_table(
    insights_api, monkeypatch
) -> None:
    body = _insights(insights_api, monkeypatch, f"SELECT {GHOST} FROM inventory WHERE sku = 'x'")

    _assert_no_invented(body)
    assert body["status"] == "REFUSE", body
    assert f"column guardrail: UNKNOWN_COLUMN:{WITHHELD}" in body["answer"], body["answer"]
    for text in _free_text(body):
        assert GHOST not in text
    # Ranked (granted) names are never withheld: the ranking is untouched.
    tables = {row["where"]["table"] for row in body["ontology"]["locations"]}
    assert "inventory" in tables


def test_insights_schema_context_invented_table_is_withheld(insights_api, monkeypatch) -> None:
    body = _insights(
        insights_api,
        monkeypatch,
        f"SELECT {BAND} FROM {VAULT} WHERE qty > 1",
        intent=SCHEMA_INTENT,
        schema=SCHEMA,
    )

    _assert_no_invented(body)
    code = "schema_context:ungranted_table:" + WITHHELD
    assert body["status"] == "REFUSE"
    assert body["answer"] == code
    assert body["generative"]["refuse_reason"] == code
    assert not body.get("sql_used")


def test_insights_schema_context_keeps_the_callers_declared_table(
    insights_api, monkeypatch
) -> None:
    body = _insights(
        insights_api,
        monkeypatch,
        f"SELECT {GHOST} FROM alpha_ledger WHERE qty > 1",
        intent=SCHEMA_INTENT,
        schema=SCHEMA,
    )

    _assert_no_invented(body)
    # alpha_ledger is declared by the caller's schema_context: it is the caller's name.
    assert body["answer"] == f"column {WITHHELD} is not declared for alpha_ledger", body["answer"]
    assert body["generative"]["refuse_reason"] == body["answer"]


def test_insights_keeps_a_name_from_the_callers_own_question(insights_api, monkeypatch) -> None:
    intent = f"how many skus by {GHOST}"
    body = _insights(
        insights_api, monkeypatch, f"SELECT {GHOST} FROM inventory WHERE sku = 'x'", intent=intent
    )

    assert body["intent"] == intent
    assert f"UNKNOWN_COLUMN:{GHOST}" in body["answer"], body["answer"]


def test_insights_validated_sql_and_granted_names_are_unchanged(insights_api, monkeypatch) -> None:
    """Positive control: a valid statement is returned exactly as the model wrote it."""
    body = _insights(insights_api, monkeypatch, SQL_OK, intent=SCHEMA_INTENT, schema=SCHEMA)

    assert body["status"] == "ABSTAIN", body
    # The guardrail's row cap is the only change to the model's statement.
    assert body["sql_used"] in (SQL_OK, SQL_OK + " LIMIT 1000"), body["sql_used"]
    assert body["generative"]["sql"] == body["sql_used"]
    assert "alpha_ledger" in body["generative"]["sql"]
    assert WITHHELD not in json.dumps(body)
    assert body["answer"].startswith("Validated SQL via FreeRoute"), body["answer"]
    lake = _test_lake()
    assert sorted(lake.execute(body["sql_used"]).fetchall()) == _oracle_qty_over_one()


def test_insights_pack_sql_is_unchanged(insights_api, monkeypatch) -> None:
    # Written as the column guardrail renders it, so only its row cap is added.
    sql = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory WHERE NOT sku IS NULL"
    body = _insights(insights_api, monkeypatch, sql)

    assert body["status"] == "ABSTAIN", body
    assert body["sql_used"] in (sql, sql + " LIMIT 1000"), body["sql_used"]
    assert body["generative"]["sql"] == body["sql_used"]
    assert WITHHELD not in json.dumps(body)
    oracle = _oracle(sql)
    assert oracle and oracle[0]["sku_count"] > 0
    assert _rowset(_oracle(body["sql_used"])) == _rowset(oracle)


# -- MCP answer_engine.answer ------------------------------------------------------


@pytest.fixture
def mcp_l2(monkeypatch: pytest.MonkeyPatch):
    """The real DMS L2 port behind answer(), as tests/dms/test_l2_freeroute_envelope.py drives it."""
    from bench.accuracy import _ensure_db_loaded
    from CortexOS.dms import l2_generation
    from packs.dms.generative.l2_adapter import DmsL2Generation

    _ensure_db_loaded()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.delenv("DMS_L2_SHADOW", raising=False)
    port = DmsL2Generation()
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: port)
    monkeypatch.setattr("CortexOS.dms.answer_engine.match_certified", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.route_to_metric", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.undefined_subject", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine._shape_refusal", lambda q: None)
    monkeypatch.setattr("packs.dms.semantic.query_skills.find", lambda *a, **k: None)
    monkeypatch.setattr("packs.dms.semantic.catalog_answer.is_catalog_intent", lambda q: False)


MCP_SESSION = "name-echo-mcp"


def _mcp_answer(client: TestClient, question: str = MCP_QUESTION) -> dict[str, Any]:
    res = client.post(
        "/mcp/call",
        json={
            "name": "answer_engine.answer",
            "arguments": {"question": question, "session_id": MCP_SESSION},
        },
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["ok"] is True and out["name"] == "answer_engine.answer"
    return out


def test_mcp_answer_withholds_an_invented_column(armed_openvault, dms_http, mcp_l2) -> None:
    for _ in range(3):
        armed_openvault.reply(f"SELECT {GHOST} FROM inventory LIMIT 5")
    dms_http.bind_session(MCP_SESSION, {"inventory": "TRUE"})

    out = _mcp_answer(dms_http)

    _assert_no_invented(out)
    result = out["result"]
    assert result["badge"] == "abstain"
    assert result["rows"] == []
    assert result["sql_used"] is None
    assert f"UNKNOWN_COLUMN:{WITHHELD}" in result["answer"], result["answer"]
    assert f"UNKNOWN_COLUMN:{WITHHELD}" in result["assumptions"]
    # The granted table stays in the prose.
    assert "This session can answer over: inventory." in result["answer"]
    assert result["granted_sources"] == ["inventory"]


def test_mcp_answer_withholds_an_invented_table(armed_openvault, dms_http, mcp_l2) -> None:
    for _ in range(3):
        armed_openvault.reply(f"SELECT {BAND} FROM {VAULT} LIMIT 5")
    dms_http.bind_session(MCP_SESSION, {"inventory": "TRUE"})

    out = _mcp_answer(dms_http)

    _assert_no_invented(out)
    assert out["result"]["badge"] == "abstain"
    assert out["result"]["rows"] == []


def test_mcp_served_answer_keeps_rows_sql_and_answer(armed_openvault, dms_http, mcp_l2) -> None:
    """Positive control: an ordinary query serves the rows DuckDB returns, untouched."""
    sql = "select sku from inventory order by sku limit 5"
    armed_openvault.reply(sql)
    dms_http.bind_session(MCP_SESSION, {"inventory": "TRUE"})
    oracle = _oracle(sql)
    assert oracle

    out = _mcp_answer(dms_http)

    result = out["result"]
    assert result["rows"], result
    assert _rowset(result["rows"]) == _rowset(oracle)
    assert _rowset(_oracle(result["sql_used"])) == _rowset(oracle)
    for row in oracle:
        assert row["sku"] in result["answer"]
    assert WITHHELD not in json.dumps(out)


def test_mcp_served_after_an_invented_column_keeps_rows(armed_openvault, dms_http, mcp_l2) -> None:
    good = "select sku from inventory order by sku limit 5"
    armed_openvault.reply(f"SELECT {GHOST} FROM inventory LIMIT 5")
    armed_openvault.reply(good)
    dms_http.bind_session(MCP_SESSION, {"inventory": "TRUE"})
    oracle = _oracle(good)

    out = _mcp_answer(dms_http)

    _assert_no_invented(out)
    result = out["result"]
    assert _rowset(result["rows"]) == _rowset(oracle), result
    assert _rowset(_oracle(result["sql_used"])) == _rowset(oracle)
    for row in oracle:
        assert row["sku"] in result["answer"]


# -- the shared scrub on a dict envelope -------------------------------------------


def test_scrub_envelope_changes_free_text_only() -> None:
    """Codes, rows, values, SQL and the question are never touched, even on a name match."""
    from CortexOS.api.response_scrub import scrub_envelope
    from CortexOS.sql_attempts import AttemptRecord

    sql = f"SELECT abstain, {GHOST} FROM {VAULT}"
    envelope = {
        "status": "ABSTAIN",
        "badge": "abstain",
        "check": "abstain",
        "intent": "count abstain rows",
        "question": f"why {VAULT}",
        "answer": f"Abstained: UNKNOWN_TABLE:{VAULT}",
        "sql_used": None,
        "rows": [],
        "values": [],
        "generative": {"sql": sql, "refuse_reason": f"UNKNOWN_COLUMN:{GHOST}", "check": GHOST},
        "validation": {"unsure": [{"id": GHOST, "kind": "sql", "why": f"reads {VAULT}"}]},
        "ontology": {"locations": [{"id": VAULT}]},
        "suggestions": [f"try {GHOST}"],
    }
    out = scrub_envelope(
        envelope,
        AttemptRecord(sql=[sql]),
        verified=None,
        caller_texts=[envelope["intent"]],
    )

    assert out["answer"] == f"Abstained: UNKNOWN_TABLE:{WITHHELD}"
    assert out["generative"]["refuse_reason"] == f"UNKNOWN_COLUMN:{WITHHELD}"
    assert out["validation"]["unsure"][0]["why"] == f"reads {WITHHELD}"
    assert out["suggestions"] == [f"try {WITHHELD}"]
    # Structure is left as it is.
    for key in ("status", "badge", "check", "intent", "question", "rows", "values", "ontology"):
        assert out[key] == envelope[key], key
    assert out["generative"]["sql"] == sql
    assert out["generative"]["check"] == GHOST
    assert out["validation"]["unsure"][0]["id"] == GHOST


def test_scrub_envelope_keeps_a_served_answer_and_returns_the_same_object_when_clean() -> None:
    from CortexOS.api.response_scrub import scrub_envelope
    from CortexOS.sql_attempts import AttemptRecord

    served = {
        "answer": f"sku=A, {GHOST}=1",
        "sql_used": f"SELECT sku FROM {VAULT}",
        "rows": [{"sku": "A"}],
        "assumptions": f"read {VAULT}",
    }
    out = scrub_envelope(served, AttemptRecord(sql=[served["sql_used"]]), verified=None)
    assert out["answer"] == served["answer"]
    assert out["rows"] == served["rows"] and out["sql_used"] == served["sql_used"]
    assert out["assumptions"] == f"read {WITHHELD}"

    clean = {"answer": "sku=A", "rows": [{"sku": "A"}]}
    record = AttemptRecord(sql=["SELECT sku FROM t"])
    # sku is a row key and t a name the caller declared: nothing to withhold.
    assert scrub_envelope(clean, record, verified=None, caller_names=["t"]) is clean


def test_a_repeated_statement_is_noted_once() -> None:
    from CortexOS.sql_attempts import note_error, note_sql, recording

    sql = f"SELECT {GHOST} FROM {VAULT}"
    with recording() as attempts:
        note_sql(sql)
        note_sql(sql)
        note_error("boom")
        note_error("boom")
    assert attempts.sql == [sql]
    assert attempts.errors == ["boom"]
