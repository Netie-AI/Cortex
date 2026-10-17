"""DB-ERROR-TEXT-CX-01: raw database error text never reaches the user or the model.

DuckDB error text can carry ``LINE 1:`` caret context cut from the
post-enforce SQL, so a row-predicate literal from the signed grant can ride
along. Sampled column values can ride along on an empty-result detail. The
served envelope and every prompt the scripted OpenVault received are checked.
The error-fed retry must still name the error class and still answer.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from CortexOS.dms import plan_sql_ask
from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401
from tests.test_plan_sql.test_c_loop_a_plan_sql import (  # noqa: F401
    GOOD_SQL,
    SESSION,
    _ask,
    _assert_rung,
    _json,
    _plan_sql_on,
    _reply,
    _stronger_route,
)

CANARY = "SECRET-LOC-9"
GRANT = {"transactions": f"location_id <> '{CANARY}'"}
# Passes the semantic guard; DuckDB's binder rejects it at EXPLAIN, and the
# caret context it prints sits right after the enforced row predicate.
EXPLAIN_FAILS = (
    "SELECT txn_type, COUNT(*) AS n FROM transactions "
    "WHERE txn_type > 5 GROUP BY txn_type"
)
EMPTY_SQL = (
    "SELECT txn_type, COUNT(*) AS n FROM transactions "
    "WHERE sku = 'SKU-NOT-THERE' GROUP BY txn_type"
)
STRONGER = "openai/gpt-oss-20b"


def _prompts(fake) -> list[str]:
    return [
        str(message.get("content") or "")
        for call in fake.chat_calls
        for message in call["body"]["messages"]
    ]


def _oracle() -> list[dict[str, Any]]:
    import duckdb

    from CortexOS.execution.warehouse import DEFAULT_DB

    con = duckdb.connect(str(DEFAULT_DB), read_only=True)
    try:
        rel = con.execute(
            "SELECT txn_type, COUNT(*) AS n FROM transactions "
            f"WHERE location_id <> '{CANARY}' GROUP BY txn_type ORDER BY n DESC"
        )
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=False)) for row in rel.fetchall()]
    finally:
        con.close()


def test_must_fail_explain_error_text_never_reaches_envelope_or_prompts(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    _stronger_route(armed_openvault, STRONGER, monkeypatch)
    _reply(armed_openvault, EXPLAIN_FAILS)
    _reply(armed_openvault, EXPLAIN_FAILS)
    _reply(armed_openvault, EXPLAIN_FAILS, model=STRONGER)
    # No WHY line: the envelope falls back to the last failure detail.
    _reply(armed_openvault, "CLOSEST: how many rows are in transactions", model=STRONGER)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["route"] == "reconfirm" and body["rows"] in ([], None)
    assert plan_sql_ask.GATE_REFUSED in body["reconfirm"]["why"]
    served = json.dumps(body)
    assert CANARY not in served
    assert "LINE 1:" not in served
    prompts = _prompts(armed_openvault)
    assert len(armed_openvault.chat_calls) == 4
    for prompt in prompts:
        assert CANARY not in prompt
        assert "LINE 1:" not in prompt
    _assert_rung(body, plan_sql_ask.RUNG_RECONFIRM)


def test_explain_error_retry_still_names_the_class_and_answers(
    armed_openvault, dms_http  # noqa: F811
) -> None:
    """Positive control: the redacted error still drives a working retry."""
    _reply(armed_openvault, EXPLAIN_FAILS)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    oracle = _oracle()
    assert oracle
    assert [(r["txn_type"], int(r["n"])) for r in body["rows"]] == [
        (r["txn_type"], int(r["n"])) for r in oracle
    ]
    for row in oracle:
        assert f"txn_type={row['txn_type']}, n={row['n']}" in body["answer"]
    retry = armed_openvault.chat_calls[1]["body"]["messages"][1]["content"]
    assert "EXPLAIN_FAILED:BINDER_ERROR" in retry
    assert CANARY not in retry and "LINE 1:" not in retry
    assert CANARY not in json.dumps(body)
    _assert_rung(body, plan_sql_ask.rung_retry(1))


def test_must_fail_sampled_values_never_reach_the_user(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from CortexOS.execution import submit

    real = submit.execute_sql
    sampled: list[str] = []

    def spy(verified, sql, **kwargs):  # noqa: ANN001
        out = real(verified, sql, **kwargs)
        if sql.lstrip().upper().startswith('SELECT DISTINCT "SKU"'):
            sampled.extend(str(row["v"]) for row in out[0])
        return out

    monkeypatch.setattr(submit, "execute_sql", spy)
    _stronger_route(armed_openvault, STRONGER, monkeypatch)
    _reply(armed_openvault, EMPTY_SQL)
    _reply(armed_openvault, EMPTY_SQL)
    _reply(armed_openvault, EMPTY_SQL, model=STRONGER)
    _reply(armed_openvault, "CLOSEST: how many rows are in transactions", model=STRONGER)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["route"] == "reconfirm"
    assert plan_sql_ask.EMPTY_RESULT in body["reconfirm"]["why"]
    assert "SKU-NOT-THERE" in body["reconfirm"]["why"]
    assert sampled
    served = json.dumps(body)
    for value in sampled:
        assert value not in served
    # The model still sees the grounded values in its own prompt block.
    retry = armed_openvault.chat_calls[1]["body"]["messages"][1]["content"]
    assert plan_sql_ask.EMPTY_RESULT in retry and "SKU-NOT-THERE" in retry
    assert sampled[0] in retry


@pytest.mark.parametrize(
    ("raw", "code", "kept"),
    [
        (
            'Binder Error: Referenced column "last_audit_date" not found in FROM clause!\n'
            'Candidate bindings: "supplier_id"\n\n'
            "LINE 1: ...FROM (SELECT * FROM suppliers WHERE region = 'APAC-7') AS s WHERE ...\n"
            "                                                           ^",
            "BINDER_ERROR",
            '"last_audit_date"',
        ),
        (
            "Conversion Error: Could not convert string 'jane@example.com' to INT32\n\n"
            "LINE 1: SELECT CAST(email AS INT) FROM (SELECT * FROM t WHERE x = 'K-1') ...",
            "CONVERSION_ERROR",
            "Could not convert string",
        ),
        ("something without a class 'v-1'", "DB_ERROR", "something without a class"),
    ],
)
def test_redact_db_error_masks_literals_and_drops_line_context(
    raw: str, code: str, kept: str
) -> None:
    from CortexOS.dms.sql_validate_gate import redact_db_error

    text = redact_db_error(raw)
    assert text.startswith(f"{code}:")
    assert kept in text
    for leaked in ("LINE", "^", "APAC-7", "jane@example.com", "K-1", "v-1", "SELECT", "\n"):
        assert leaked not in text, text


def test_must_fail_gate_and_execute_seams_carry_only_redacted_text() -> None:
    """The shared seams every caller reads (L2 prompts, answer engine, submit)."""
    from bench.accuracy import _ensure_db_loaded
    from CortexOS.dms.sql_validate_gate import SqlGateAbstain, run_gate
    from CortexOS.dms.warehouse_db import load_semantic_layer
    from CortexOS.execution.submit import execute_sql
    from CortexOS.execution.warehouse import DEFAULT_DB, get_connection
    from tests.dms.test_c7_02_manifest_before_explain import _verified

    _ensure_db_loaded()
    verified = _verified(GRANT)
    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        gate = run_gate(EXPLAIN_FAILS, load_semantic_layer(), con=con, verified=verified)
    finally:
        con.close()
    assert gate.passed is False
    assert gate.violations[0].startswith("EXPLAIN_FAILED:BINDER_ERROR:")
    assert CANARY not in " ".join(gate.violations) and "LINE 1:" not in " ".join(gate.violations)

    with pytest.raises(SqlGateAbstain) as caught:
        execute_sql(verified, EXPLAIN_FAILS, explain_gate=True)
    shown = f"{caught.value} {caught.value.violations}"
    assert "BINDER_ERROR" in shown
    assert CANARY not in shown and "LINE 1:" not in shown
    # The raw engine text is kept for the audit sink, not dropped.
    assert CANARY in (caught.value.raw_detail or "")
