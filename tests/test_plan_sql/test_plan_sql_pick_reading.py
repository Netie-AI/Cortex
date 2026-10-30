"""PICK-READING-01: a split reconfirm offers its readings, and the user can pick one.

Founder ruling 2026-10-29 (CLAUDE.md section 9): when two SQL readings
disagree, the reconfirm may offer up to three of them. Each is a plain-language
description with a row count and no values, and a picked reading runs again
through every gate under the session grant. Contract 1.10.0,
``CORTEX_PLAN_SQL_READINGS`` default off.

Every test goes through ``POST /v1/contract/ask`` and asserts the customer
envelope: badge, the rendered answer text and the rows (CLAUDE.md section 8).
The model is the scripted OpenVault. Offers are checked for values with the
rule below: no returned cell of any reading reaches a free-text field.
"""

# ruff: noqa: F811  (pytest fixtures are imported, then requested by name)
from __future__ import annotations

import dataclasses
import json
import re
from decimal import Decimal
from typing import Any

import pytest
from cortex_contract.answer import PlanSqlReading

from CortexOS.dms import plan_sql_ask, plan_sql_vote
from CortexOS.plan_sql import describe as plan_describe
from CortexOS.plan_sql import readings as plan_readings
from tests.dms.test_c7_02_manifest_before_explain import _badge, dms_http  # noqa: F401
from tests.test_plan_sql.test_ai_sql_reads_table import _oracle, _rowset
from tests.test_plan_sql.test_c_loop_a_plan_sql import (  # noqa: F401
    GOOD_SQL,
    GRANT,
    PAYLOAD,
    QUESTION,
    SESSION,
    _ask,
    _json,
    _plan_sql_on,
    _ran,
    _reply,
    _stronger_route,
    execute_spy,
)
from tests.test_plan_sql.test_plan_sql_vote import (
    BAD_SQL,
    KG_SQL,
    SAME_ROWS_SQL,
    SKU_SQL,
    STRONG,
    _same_prompt_for_both_readings,
)

MODEL = "openai/gpt-oss-120b"
CLOSEST = "how many transactions are there of each txn_type"
WHY = "count rows, SKUs or kilograms is unclear"
SPLIT = (GOOD_SQL, SKU_SQL, KG_SQL)
# A bucket reading whose result cells are its CASE labels (verified against
# the test lake: it passes the gate, lineage and literal grounding).
BUCKET_SQL = (
    "SELECT CASE WHEN quantity_kg > 100 THEN 'heavy' ELSE 'light' END AS size, "
    "COUNT(*) AS n FROM transactions "
    "GROUP BY CASE WHEN quantity_kg > 100 THEN 'heavy' ELSE 'light' END"
)
# Amendment r3: a model comment is a value channel, so the same reading with
# its labels in a comment must not print them either.
BUCKET_SQL_COMMENTED = (
    "SELECT CASE WHEN quantity_kg /* heavy or light */ > 100 THEN 'heavy' "
    "ELSE 'light' END AS size, COUNT(*) AS n FROM transactions "
    "GROUP BY CASE WHEN quantity_kg /* heavy or light */ > 100 THEN 'heavy' ELSE 'light' END"
)
SCHEMA_CONTEXT = "\n".join(
    [
        "DIALECT: generic",
        "SCHEMA",
        "- transactions reason=score:1",
        "- txn_type VARCHAR reason=kept",
        "- sku VARCHAR reason=kept",
        "- quantity_kg DOUBLE reason=kept",
    ]
)
READING_KEYS = {"reading_id", "statement", "row_count", "expires_at"}
UNBOUND = "plan_sql_reading_unbound"
UNAVAILABLE = "plan_sql_reading_unavailable"


@pytest.fixture(autouse=True)
def _readings_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(plan_sql_vote.ENABLED_ENV, "1")
    monkeypatch.setenv(plan_readings.READINGS_ENV, "1")
    plan_readings.reset_for_tests()
    yield
    plan_readings.reset_for_tests()


# -- helpers ---------------------------------------------------------------------


def _script_split(
    fake, monkeypatch: pytest.MonkeyPatch, sqls: tuple[str, str, str] = SPLIT
) -> None:
    """Three readings that disagree: first model twice, then the stronger route."""
    _stronger_route(fake, STRONG, monkeypatch)
    _reply(fake, sqls[0])
    _reply(fake, sqls[1])
    _reply(fake, sqls[2], model=STRONG)
    _reply(fake, f"WHY: {WHY}\nCLOSEST: {CLOSEST}", model=STRONG)


def _offer(client, **extra: Any) -> dict[str, Any]:
    body = _json(_ask(client, **extra))
    _assert_offer_envelope(body)
    return body


def _pick(client, reading_id: str, **extra: Any):
    return _ask(client, plan_sql_reading_id=reading_id, **extra)


def _readings(body: dict[str, Any]) -> list[dict[str, Any]]:
    return list((body.get("reconfirm") or {}).get("readings") or [])


def _held(reading_id: str) -> plan_readings.HeldReading:
    held = plan_readings.store().get(reading_id)
    assert held is not None
    return held


def _steps(body: dict[str, Any]) -> str:
    return "\n".join(body["assumptions"])


def _readings_note(body: dict[str, Any]) -> str:
    """The reason on the ``step readings:`` line: ``offered=N`` or why none was offered."""
    (line,) = [a for a in body["assumptions"] if a.startswith("step readings:")]
    assert line.endswith(")"), line
    return line[line.index("(offered=") + 1 : -1]


def _assert_offer_envelope(body: dict[str, Any]) -> None:
    """A reconfirm that asks: no rows, no SQL, no token, no sources."""
    assert _badge(body) == "reconfirm", body
    assert body["route"] == "reconfirm"
    assert body["provenance"]["badge"] == "reconfirm"
    assert body["rows"] in ([], None)
    assert body["row_count"] == 0
    assert body["sql_used"] is None
    assert body["drillthrough_token"] is None
    assert body["contributing_sources"] == []
    assert body["plan_sql_rung"] == plan_sql_ask.RUNG_RECONFIRM
    assert body["answer"].startswith("Reconfirm:"), body["answer"]
    assert "not found in the database" in body["answer"]


def _assert_readings_offered(body: dict[str, Any], sqls: tuple[str, ...]) -> None:
    readings = _readings(body)
    assert len(readings) == len(sqls), body["reconfirm"]
    statements = [reading["statement"] for reading in readings]
    assert len(set(statements)) == len(statements)
    for i, (reading, sql) in enumerate(zip(readings, sqls, strict=True), 1):
        assert set(reading) == READING_KEYS, reading
        PlanSqlReading.model_validate(reading)
        assert len(reading["statement"]) <= 320
        assert reading["row_count"] == len(_oracle(sql))
        count = reading["row_count"]
        shown = f"({i}) {reading['statement']}, {count} {'row' if count == 1 else 'rows'}."
        assert shown in body["answer"], body["answer"]
        # The id is opaque: no SQL and no table text in it.
        for word in ("select", "from", "transactions", "txn_type", "sku"):
            assert word not in reading["reading_id"].lower()
    assert body["answer"] == plan_sql_ask.reconfirm_answer(
        body["reconfirm"]["why"],
        body["reconfirm"]["closest_question"],
        [(r["statement"], r["row_count"]) for r in readings],
    )
    assert _readings_note(body) == f"offered={len(sqls)}", body["assumptions"]


# -- the no-values rule ------------------------------------------------------------


def _cells(sql: str) -> list[Any]:
    """Result cells that could identify a value: numbers >= 10 or fractional, strings >= 2."""
    found: list[Any] = []
    for row in _oracle(sql):
        for value in row.values():
            if value is None or isinstance(value, bool):
                continue
            if isinstance(value, (int, float, Decimal)):
                number = float(value)
                if abs(number) >= 10 or number != int(number):
                    found.append(value)
            elif len(str(value)) >= 2:
                found.append(value)
    return found


def _renderings(value: Any) -> set[str]:
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        texts = {repr(number)}
        if number == int(number):
            texts.add(str(int(number)))
        return texts
    return {str(value)}


def _free_text(body: dict[str, Any]) -> list[str]:
    """The fields the response scrub treats as text, plus every statement."""
    reconfirm = body.get("reconfirm") or {}
    texts = [
        body.get("answer") or "",
        reconfirm.get("why") or "",
        reconfirm.get("closest_question") or "",
        *(r["statement"] for r in reconfirm.get("readings") or []),
        *(re.sub(r"call=\S+", "", line) for line in body.get("assumptions") or []),
        *(body.get("suggestions") or []),
        body.get("served_reason") or "",
        (body.get("provenance") or {}).get("assumptions") or "",
    ]
    return texts


def _assert_no_values(body: dict[str, Any], sqls: tuple[str, ...], question: str) -> list[str]:
    """No result cell of any reading appears, token-bounded, in any free-text field.

    Exempt only: a cell the shipped display predicate keeps, the reading's
    displayed row count, or the LIMIT N its statement shows. Returns the
    cells checked, so a test can show it was not vacuous.
    """
    keep = plan_sql_ask._display_literal(question)
    fields = _free_text(body)
    checked: list[str] = []
    for sql, reading in zip(sqls, _readings(body), strict=True):
        limit = re.search(r"\bfirst (\d+) rows?\b", reading["statement"])
        exempt = {str(reading["row_count"])} | ({limit.group(1)} if limit else set())
        for cell in _cells(sql):
            for text in _renderings(cell):
                if text in exempt or keep(text):
                    continue
                checked.append(text)
                pattern = re.compile(rf"(?<![\w.]){re.escape(text)}(?![\w.])", re.IGNORECASE)
                for field in fields:
                    assert not pattern.search(field), (text, field)
    return checked


def _assert_served_pick(
    body: dict[str, Any], sql: str, reading_id: str, statement: str, index: int, of: int
) -> None:
    oracle = _oracle(sql)
    assert body["provenance"]["badge"] == "session"
    assert _badge(body) == "session"
    assert _rowset(body["rows"]) == _rowset(oracle)
    assert body["row_count"] == len(oracle)
    assert body["sql_used"] == _held(reading_id).served_sql
    top = body["rows"][0]
    assert str(next(iter(top.values()))) in body["answer"], body["answer"]
    assert not body["answer"].startswith(("Reconfirm:", "Not answered", "No rows"))
    assert body["drillthrough_token"]
    assert body["audit_id"]
    assert [s["kind"] for s in body["contributing_sources"]] == ["table"]
    assert body["reconfirm"] is None
    assert body["plan_sql_reading_id"] == reading_id
    assert body["model_calls"] is None
    assert body["assumptions"][0] == f"reading: {statement}"
    lines = body["assumptions"]
    assert any(
        line.startswith("step vote:")
        and plan_sql_vote.VOTE_SPLIT in line
        and f"picked reading {index} of {of}" in line
        and "not confirmed by a second reading" in line
        for line in lines
    ), lines
    assert any(
        line.startswith("step pick:") and f"reading {index} of {of}" in line for line in lines
    ), lines
    assert any(
        line.startswith("step sample:") and "skipped: a pick re-runs held SQL" in line
        for line in lines
    ), lines
    assert plan_sql_ask.CAVEAT in lines


# -- 1, 1b: the offer --------------------------------------------------------------


def test_split_offers_readings_without_values(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dms_http.bind_session(SESSION, GRANT)

    # The same script with readings off: the counts the offer must not change.
    monkeypatch.delenv(plan_readings.READINGS_ENV)
    _script_split(armed_openvault, monkeypatch)
    off = _json(_ask(dms_http))
    off_calls, off_runs = len(armed_openvault.chat_calls), len(_ran(execute_spy))
    assert "readings" not in off["reconfirm"]

    monkeypatch.setenv(plan_readings.READINGS_ENV, "1")
    _script_split(armed_openvault, monkeypatch)
    body = _offer(dms_http)

    assert CLOSEST in body["answer"]
    _assert_readings_offered(body, SPLIT)
    checked = _assert_no_values(body, SPLIT, QUESTION)
    assert len(checked) >= 12, checked
    assert len(armed_openvault.chat_calls) - off_calls == off_calls
    assert len(_ran(execute_spy)) - off_runs == off_runs
    # Each held reading names the model that wrote it and its writer's rung.
    held = [_held(r["reading_id"]) for r in _readings(body)]
    assert [h.rung for h in held] == [
        plan_sql_ask.RUNG_PLAN,
        plan_sql_ask.RUNG_PLAN,
        plan_sql_ask.RUNG_STRONG,
    ]
    assert [h.stamp.served_model for h in held] == [MODEL, MODEL, STRONG]
    assert all(h.stamp.served_provider for h in held)
    assert not any(hasattr(h, "rows") for h in held)


@pytest.mark.parametrize(
    "bucket", [BUCKET_SQL, BUCKET_SQL_COMMENTED], ids=["case_labels", "commented_labels"]
)
def test_split_offer_never_prints_case_labels(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch, bucket: str
) -> None:
    sqls = (GOOD_SQL, bucket, KG_SQL)
    keys = {plan_sql_vote.result_key(_oracle(sql)) for sql in sqls}
    assert len(keys) == 3, "the three readings must return different rows"
    for word in ("heavy", "light", "100"):
        assert word not in QUESTION
    _script_split(armed_openvault, monkeypatch, sqls)
    dms_http.bind_session(SESSION, GRANT)

    body = _offer(dms_http)

    _assert_readings_offered(body, sqls)
    checked = _assert_no_values(body, sqls, QUESTION)
    assert {"heavy", "light"} <= set(checked)
    text = json.dumps(body).lower()
    assert "heavy" not in text and "light" not in text
    bucket_reading = _readings(body)[1]
    assert plan_describe.MARK in bucket_reading["statement"]
    for statement in (r["statement"] for r in _readings(body)):
        assert "/*" not in statement and "--" not in statement
    held = _held(bucket_reading["reading_id"])
    if bucket is BUCKET_SQL_COMMENTED:
        # The comment reached the SQL describe was given: the channel is real.
        assert "heavy or light" in held.served_sql

    # Picked, the bucket reading is served, and only then do its values appear.
    picked = _json(_pick(dms_http, bucket_reading["reading_id"]))
    _assert_served_pick(
        picked, BUCKET_SQL, bucket_reading["reading_id"], bucket_reading["statement"], 2, 3
    )
    assert picked["rows"][0]["size"] in {"heavy", "light"}


# PICK-READING-01 review: a model-written name is a value channel exactly like a
# literal. Each variant is the bucket reading with its CASE labels moved into a
# name the model chose: a quoted column alias, a bare label alias (also used in
# GROUP BY and ORDER BY), a table alias, and a derived table named with one
# label whose output column is named with the other. Each passes the gate,
# lineage and literal grounding and returns the bucket rows; the statement may
# print only the payload's granted names. Value: (SQL, the label column's key
# in the rows, the name as the held SQL spells it). The CTE form is below: the
# SQL gate refuses a CTE reference today, so it never reaches describe.
NAMED_BUCKETS: dict[str, tuple[str, str, str]] = {
    "quoted_alias": (
        "SELECT CASE WHEN quantity_kg > 100 THEN 'heavy' ELSE 'light' END "
        'AS "heavy or light", COUNT(*) AS n FROM transactions GROUP BY 1',
        "heavy or light",
        'AS "heavy or light"',
    ),
    "label_alias": (
        "SELECT CASE WHEN quantity_kg > 100 THEN 'heavy' ELSE 'light' END AS heavy, "
        "COUNT(*) AS n FROM transactions GROUP BY heavy ORDER BY heavy",
        "heavy",
        "GROUP BY heavy ORDER BY heavy",
    ),
    "table_alias": (
        "SELECT CASE WHEN heavy.quantity_kg > 100 THEN 'heavy' ELSE 'light' END AS size, "
        "COUNT(*) AS n FROM transactions AS heavy "
        "GROUP BY CASE WHEN heavy.quantity_kg > 100 THEN 'heavy' ELSE 'light' END",
        "size",
        "FROM transactions AS heavy",
    ),
    "subquery_alias": (
        "SELECT light AS light, COUNT(*) AS n FROM (SELECT CASE WHEN quantity_kg > 100 "
        "THEN 'heavy' ELSE 'light' END AS light FROM transactions) AS heavy GROUP BY light",
        "light",
        ") AS heavy GROUP BY light",
    ),
}
# The bucket reading as a CTE named with one label whose column carries the
# other, in two spellings DuckDB folds together.
CTE_BUCKET = (
    "WITH Heavy AS (SELECT CASE WHEN quantity_kg > 100 THEN 'heavy' ELSE 'light' END "
    "AS light FROM transactions) SELECT light AS light, COUNT(*) AS n FROM HEAVY GROUP BY light"
)


@pytest.mark.parametrize("variant", sorted(NAMED_BUCKETS))
def test_split_offer_never_prints_a_model_chosen_name(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    bucket, key, name = NAMED_BUCKETS[variant]
    sqls = (GOOD_SQL, bucket, KG_SQL)
    keys = {plan_sql_vote.result_key(_oracle(sql)) for sql in sqls}
    assert len(keys) == 3, "the three readings must return different rows"
    # The same labels and counts as the bucket reading: only the names differ.
    assert sorted(tuple(r.values()) for r in _oracle(bucket)) == sorted(
        tuple(r.values()) for r in _oracle(BUCKET_SQL)
    )
    for word in ("heavy", "light", "100"):
        assert word not in QUESTION
    _script_split(armed_openvault, monkeypatch, sqls)
    dms_http.bind_session(SESSION, GRANT)

    body = _offer(dms_http)

    _assert_readings_offered(body, sqls)
    checked = _assert_no_values(body, sqls, QUESTION)
    assert {"heavy", "light"} <= set(checked)
    text = json.dumps(body).lower()
    assert "heavy" not in text and "light" not in text, _readings(body)
    bucket_reading = _readings(body)[1]
    statement = bucket_reading["statement"]
    # Only fixed words, the mark, numbers and the payload's granted names.
    granted = {c["name"] for t in PAYLOAD["tables"] for c in t["columns"]} | {"transactions"}
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", statement.replace(plan_describe.MARK, "")):
        assert (
            word.lower() in plan_describe.VOCABULARY
            or word in granted
            or word in {"CASE", "WHEN", "THEN", "ELSE", "END", "computed", "value"}
        ), (word, statement)
    if variant != "subquery_alias":  # the nested-query fallback prints no tree text
        assert plan_describe.MARK in statement, statement
    held = _held(bucket_reading["reading_id"])
    assert name in held.served_sql, "the name reached describe"

    # Picked, the reading is served, and only then do its labels appear.
    picked = _json(_pick(dms_http, bucket_reading["reading_id"]))
    _assert_served_pick(picked, bucket, bucket_reading["reading_id"], statement, 2, 3)
    assert picked["rows"][0][key] in {"heavy", "light"}


def test_cte_named_reading_never_prints_its_name(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guard, not a must-fail: the SQL gate refuses the CTE reference today, so the
    reading is not offered. If the gate ever admits it, its names must still not show
    (``describe`` refuses or names it by position: test_plan_sql_describe)."""
    sqls = (GOOD_SQL, CTE_BUCKET, KG_SQL)
    assert sorted(tuple(r.values()) for r in _oracle(CTE_BUCKET)) == sorted(
        tuple(r.values()) for r in _oracle(BUCKET_SQL)
    )
    _script_split(armed_openvault, monkeypatch, sqls)
    dms_http.bind_session(SESSION, GRANT)

    body = _offer(dms_http)

    text = json.dumps(body).lower()
    assert "heavy" not in text and "light" not in text, _readings(body)
    assert _readings_note(body) in {"offered=2", "offered=3"}, body["assumptions"]
    for reading in _readings(body):
        assert "heavy" not in _held(reading["reading_id"]).statement.lower()


# PICK-READING-01 binding: a reading's statement says what its SQL computes. A name
# that is both a granted column and a projection's alias is bound as DuckDB binds
# it: ORDER BY and HAVING take the alias, GROUP BY and WHERE the column. Each
# case: (the readings, the index of the case's reading, its exact statement, a
# reading of the name the statement must not make, the clause that names it).
# The picked reading is served with ``reading: <statement>`` as its first
# assumption, so a wrong binding is a false description on a served answer.
_TOTAL_ALIAS = (
    "SELECT txn_type, SUM(quantity_kg) AS quantity_kg FROM transactions GROUP BY txn_type"
)
HAVING_ALIAS_SQL = f"{_TOTAL_ALIAS} HAVING quantity_kg > 100000"
ORDER_ALIAS_SQL = f"{_TOTAL_ALIAS} ORDER BY quantity_kg DESC LIMIT 2"
GROUP_COLUMN_SQL = (
    "SELECT SUM(quantity_kg) AS txn_type, COUNT(*) AS n FROM transactions GROUP BY txn_type"
)
ALIAS_BINDING: dict[str, tuple[tuple[str, str, str], int, str, str, str]] = {
    "having_alias": (
        (GOOD_SQL, HAVING_ALIAS_SQL, KG_SQL),
        2,
        "Returns txn_type and total of quantity_kg for each txn_type from transactions "
        f"having total of quantity_kg is more than {plan_describe.MARK}, first 1000 rows",
        "having quantity_kg",
        "HAVING quantity_kg > 100000",
    ),
    "order_alias": (
        (GOOD_SQL, SKU_SQL, ORDER_ALIAS_SQL),
        3,
        "Returns txn_type and total of quantity_kg for each txn_type from transactions, "
        "sorted by total of quantity_kg descending, txn_type, first 2 rows",
        "sorted by quantity_kg",
        "ORDER BY quantity_kg DESC",
    ),
    # Control: GROUP BY binds the column, so the alias-first rule must stay out of it.
    "group_column_control": (
        (GOOD_SQL, GROUP_COLUMN_SQL, KG_SQL),
        2,
        "Returns total of quantity_kg and number of rows for each txn_type from transactions, "
        "first 1000 rows",
        "for each total of",
        "GROUP BY txn_type",
    ),
}


@pytest.mark.parametrize("case", sorted(ALIAS_BINDING))
def test_must_fail_reading_statement_binds_names_as_duckdb_does(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    sqls, index, expected, wrong, clause = ALIAS_BINDING[case]
    sql = sqls[index - 1]
    keys = {plan_sql_vote.result_key(_oracle(s)) for s in sqls}
    assert len(keys) == 3, "the three readings must return different rows"
    _script_split(armed_openvault, monkeypatch, sqls)
    dms_http.bind_session(SESSION, GRANT)

    body = _offer(dms_http)

    _assert_readings_offered(body, sqls)
    _assert_no_values(body, sqls, QUESTION)
    reading = _readings(body)[index - 1]
    assert reading["statement"] == expected, reading["statement"]
    assert wrong not in reading["statement"]
    shown = f"({index}) {expected}, {reading['row_count']} "
    assert shown in body["answer"], body["answer"]
    # The executor adds its row cap and a tiebreak; the clause with the name is kept.
    served = _held(reading["reading_id"]).served_sql
    assert sql.split(" FROM ")[0] in served, served
    assert clause in served, served

    # Picked, the reading is served under that statement, and its rows are what
    # the statement says: per-type totals, never a single row's kilograms.
    picked = _json(_pick(dms_http, reading["reading_id"]))
    _assert_served_pick(picked, sql, reading["reading_id"], expected, index, 3)
    assert picked["assumptions"][0] == f"reading: {expected}"
    oracle = _oracle(sql)
    if case == "group_column_control":
        assert len(picked["rows"]) == len(_oracle("SELECT DISTINCT txn_type FROM transactions"))
        assert picked["rows"][0]["txn_type"] > 1000, picked["rows"]
        return
    raw_max = _oracle("SELECT MAX(quantity_kg) AS m FROM transactions")[0]["m"]
    assert all(row["quantity_kg"] > 100000 > raw_max for row in picked["rows"]), picked["rows"]
    if case == "order_alias":
        totals = [row["quantity_kg"] for row in picked["rows"]]
        assert totals == sorted(totals, reverse=True) == [r["quantity_kg"] for r in oracle]
        assert len(picked["rows"]) == 2


# -- 2, 3: byte identity -----------------------------------------------------------


def test_readings_off_split_is_unchanged(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(plan_readings.READINGS_ENV)
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)

    body = _offer(dms_http)

    assert body["reconfirm"] == {"why": WHY, "closest_question": CLOSEST}
    assert body["answer"] == plan_sql_ask.reconfirm_answer(WHY, CLOSEST)
    assert "plan_sql_reading_id" not in body
    assert "step readings:" not in _steps(body)
    assert len(plan_readings.store()) == 0


def test_readings_on_agreement_is_byte_identical(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_plan_sql.test_c_loop_a_plan_sql import _deterministic

    dms_http.bind_session(SESSION, GRANT)

    def _run() -> bytes:
        _deterministic(monkeypatch)
        _reply(armed_openvault, GOOD_SQL)
        _reply(armed_openvault, SAME_ROWS_SQL)
        resp = _ask(dms_http)
        assert resp.status_code == 200, resp.text
        return resp.content

    monkeypatch.delenv(plan_readings.READINGS_ENV)
    unset = _run()
    monkeypatch.setenv(plan_readings.READINGS_ENV, "1")
    on = _run()

    assert json.loads(on)["rows"]
    assert on == unset
    assert b"plan_sql_reading_id" not in on and b'"readings"' not in on


# -- 4, 5: the pick ----------------------------------------------------------------


@pytest.mark.parametrize("index", [1, 2, 3])
def test_pick_serves_the_chosen_reading_without_a_model_call(
    armed_openvault,
    dms_http,
    execute_spy: list[str],
    monkeypatch: pytest.MonkeyPatch,
    index: int,
) -> None:
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)
    offered = _readings(_offer(dms_http))
    reading = offered[index - 1]
    calls, spied = len(armed_openvault.chat_calls), len(execute_spy)

    body = _json(_pick(dms_http, reading["reading_id"]))

    _assert_served_pick(
        body, SPLIT[index - 1], reading["reading_id"], reading["statement"], index, 3
    )
    assert len(armed_openvault.chat_calls) == calls
    new = execute_spy[spied:]
    assert len(new) == 1 and new[0] == body["sql_used"]
    assert not any(sql.lstrip().upper().startswith("SELECT DISTINCT") for sql in new)
    assert (body["plan_sql_rung"] == plan_sql_ask.RUNG_STRONG) == (body["served_model"] == STRONG)
    held = _held(reading["reading_id"])
    assert body["plan_sql_rung"] == held.rung
    assert f"rung: {held.rung}" in body["assumptions"]
    assert body["provenance"]["query_source"] == (
        f"openvault-freeroute:{held.stamp.served_call_id}"
    )


@pytest.mark.parametrize(
    ("second", "writer"),
    [
        pytest.param((SKU_SQL,), plan_sql_ask.RUNG_PLAN, id="first_write"),
        pytest.param((BAD_SQL, SKU_SQL), plan_sql_ask.rung_retry(1), id="rewrite"),
    ],
)
def test_escalation_round_split_names_each_readings_writer(
    armed_openvault,
    dms_http,
    monkeypatch: pytest.MonkeyPatch,
    second: tuple[str, ...],
    writer: str,
) -> None:
    """The vote runs in the stronger round; its second reading is still the first model's."""
    _stronger_route(armed_openvault, STRONG, monkeypatch)
    _reply(armed_openvault, BAD_SQL)
    _reply(armed_openvault, BAD_SQL)
    _reply(armed_openvault, GOOD_SQL, model=STRONG)
    for sql in second:
        _reply(armed_openvault, sql)
    _reply(armed_openvault, KG_SQL, model=STRONG)
    _reply(armed_openvault, f"WHY: {WHY}\nCLOSEST: {CLOSEST}", model=STRONG)
    dms_http.bind_session(SESSION, GRANT)

    offer = _offer(dms_http)
    _assert_readings_offered(offer, (GOOD_SQL, SKU_SQL, KG_SQL))
    readings = _readings(offer)
    assert [_held(r["reading_id"]).rung for r in readings] == [
        plan_sql_ask.RUNG_STRONG,
        writer,
        plan_sql_ask.RUNG_STRONG,
    ]

    body = _json(_pick(dms_http, readings[1]["reading_id"]))

    _assert_served_pick(body, SKU_SQL, readings[1]["reading_id"], readings[1]["statement"], 2, 3)
    assert body["plan_sql_rung"] == writer
    assert body["served_model"] == MODEL
    first = _json(_pick(dms_http, readings[0]["reading_id"]))
    assert first["plan_sql_rung"] == plan_sql_ask.RUNG_STRONG
    assert first["served_model"] == STRONG


# -- 6, 7: binding and client SQL ----------------------------------------------------


def _other_payload() -> dict[str, Any]:
    payload = json.loads(json.dumps(PAYLOAD))
    payload["tables"][0]["description"] = "stock movements"
    return payload


# (pick request extras, rebind grant for the pick, how to age the hold)
_BINDING = {
    "unknown_id": {"reading_id": "not-a-held-reading"},
    "other_session": {"session_id": "c-loop-a-303-other"},
    "rebound_manifest": {"rebind": True},
    "other_question": {"question": "how many transactions are there by type"},
    "other_payload": {"payload": "other"},
    "other_schema_context": {"schema_context": SCHEMA_CONTEXT},
    "other_scored_pack": {"scored_pack_id": "pack-pick-reading"},
    "expired": {"expired": True},
}


@pytest.mark.parametrize("case", sorted(_BINDING))
def test_must_fail_reading_binding(
    armed_openvault,
    dms_http,
    execute_spy: list[str],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """A hold runs only for the session, Space, manifest, request and pack that
    offered it, before it expires. Every other pick is the same 409, with no
    execute and no model call."""
    spec = _BINDING[case]
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)
    reading = _readings(_offer(dms_http))[0]
    reading_id = spec.get("reading_id", reading["reading_id"])
    session = spec.get("session_id", SESSION)
    if session != SESSION:
        dms_http.bind_session(session, GRANT)
    if spec.get("rebind"):
        dms_http.bind_session(SESSION, GRANT)
    if spec.get("expired"):
        held = _held(reading_id)
        plan_readings.store().replace(dataclasses.replace(held, expires_at=held.expires_at - 3600))
    extra: dict[str, Any] = {"session_id": session}
    for key in ("question", "schema_context", "scored_pack_id"):
        if key in spec:
            extra[key] = spec[key]
    if spec.get("payload") == "other":
        extra["payload"] = _other_payload()
    calls, spied = len(armed_openvault.chat_calls), len(execute_spy)

    resp = _pick(dms_http, reading_id, **extra)

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == {
        "code": UNBOUND,
        "message": "this reading is no longer available; ask the question again",
    }
    assert len(execute_spy) == spied
    assert len(armed_openvault.chat_calls) == calls


def test_must_fail_client_sql_is_ignored(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pick runs only the held reading: SQL the client sends is never run."""
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)
    reading = _readings(_offer(dms_http))[0]
    spied = len(execute_spy)

    body = _json(
        _pick(
            dms_http,
            reading["reading_id"],
            sql="SELECT sku FROM inventory LIMIT 5",
            sql_used="SELECT sku FROM inventory LIMIT 5",
        )
    )

    _assert_served_pick(body, GOOD_SQL, reading["reading_id"], reading["statement"], 1, 3)
    assert all("inventory" not in sql.lower() for sql in execute_spy[spied:])


# -- 8, 9, 10: a pick re-runs every gate --------------------------------------------


def _tamper(reading_id: str, text: str) -> None:
    held = _held(reading_id)
    plan_readings.store().replace(dataclasses.replace(held, model_text=text))


def test_must_fail_pick_regates_held_sql(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The held text goes through the SQL gate again: a file read is refused, never run."""
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)
    reading = _readings(_offer(dms_http))[0]
    _tamper(reading["reading_id"], "SELECT read_csv_auto('/etc/passwd') FROM transactions")
    calls, spied = len(armed_openvault.chat_calls), len(execute_spy)

    body = _json(_pick(dms_http, reading["reading_id"]))

    assert _badge(body).lower() == "abstain", body
    assert body["rows"] in ([], None) and body["sql_used"] is None
    assert body["drillthrough_token"] is None
    assert plan_sql_ask.MANIFEST_REFUSED in body["answer"]
    assert "MANIFEST:" in body["answer"]
    assert body["plan_sql_reading_id"] == reading["reading_id"]
    assert execute_spy[spied:] == []
    assert len(armed_openvault.chat_calls) == calls


def test_must_fail_pick_outside_payload_is_not_executed(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Granted but not in the payload: the pick reconfirms, and nothing runs."""
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, {"transactions": "TRUE", "inventory": "TRUE"})
    reading = _readings(_offer(dms_http))[0]
    _tamper(reading["reading_id"], "SELECT sku FROM inventory LIMIT 5")
    calls, spied = len(armed_openvault.chat_calls), len(execute_spy)

    body = _json(_pick(dms_http, reading["reading_id"]))

    _assert_offer_envelope(body)
    assert plan_sql_ask.OUTSIDE_PAYLOAD in body["reconfirm"]["why"]
    assert plan_sql_ask.OUTSIDE_PAYLOAD in body["answer"]
    assert "readings" not in body["reconfirm"]
    assert body["plan_sql_reading_id"] == reading["reading_id"]
    assert body["model_calls"] is None
    assert execute_spy[spied:] == []
    assert len(armed_openvault.chat_calls) == calls


def test_pick_that_now_returns_no_rows_reconfirms(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The data changed under the hold: never served, never an abstain, no model call."""
    from CortexOS.execution import submit

    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)
    reading = _readings(_offer(dms_http))[0]
    calls = len(armed_openvault.chat_calls)
    monkeypatch.setattr(submit, "execute_sql", lambda verified, sql, **kw: ([], None, None))

    body = _json(_pick(dms_http, reading["reading_id"]))

    _assert_offer_envelope(body)
    assert plan_sql_ask.EMPTY_RESULT in body["reconfirm"]["why"]
    assert "readings" not in body["reconfirm"]
    assert body["model_calls"] is None
    assert body["plan_sql_reading_id"] == reading["reading_id"]
    assert body["served_provider"] is None and body["served_model"] is None
    assert len(armed_openvault.chat_calls) == calls


# -- 11, 12: request errors and memory ------------------------------------------------


@pytest.mark.parametrize(
    "flag",
    [plan_sql_ask.ENABLED_ENV, plan_sql_vote.ENABLED_ENV, plan_readings.READINGS_ENV],
)
def test_pick_with_a_switch_off_is_409_unavailable(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    dms_http.bind_session(SESSION, GRANT)
    monkeypatch.delenv(flag)

    resp = _pick(dms_http, "any-reading")

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == UNAVAILABLE
    assert armed_openvault.chat_calls == []
    assert _ran(execute_spy) == []


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        pytest.param({"payload": None}, "reading_needs_payload", id="no_payload"),
        pytest.param({"plan_sql_confirm": "yes"}, "reading_excludes_confirm", id="with_yes"),
        pytest.param({"plan_sql_confirm": "no"}, "reading_excludes_confirm", id="with_no"),
    ],
)
def test_pick_request_errors(armed_openvault, dms_http, extra: dict[str, Any], error: str) -> None:
    dms_http.bind_session(SESSION, GRANT)

    resp = _pick(dms_http, "any-reading", **extra)

    assert resp.status_code == 422, resp.text
    assert any(err["type"] == error for err in resp.json()["detail"]), resp.json()
    assert armed_openvault.chat_calls == []


@pytest.mark.parametrize("reading_id", ["", "r" * 65], ids=["empty", "too_long"])
def test_pick_id_length_is_422(armed_openvault, dms_http, reading_id: str) -> None:
    dms_http.bind_session(SESSION, GRANT)

    resp = _pick(dms_http, reading_id)

    assert resp.status_code == 422, resp.text
    assert armed_openvault.chat_calls == []


def test_pick_bypasses_space_memory(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.dms import space_memory_ask
    from CortexOS.memory import space_memory as sm

    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)
    reading = _readings(_offer(dms_http))[0]
    monkeypatch.setenv(sm.ENABLED_ENV, "1")

    def tripwire(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a pick read per-Space memory")

    monkeypatch.setattr(space_memory_ask, "try_solution_reuse", tripwire)

    body = _json(_pick(dms_http, reading["reading_id"]))

    _assert_served_pick(body, GOOD_SQL, reading["reading_id"], reading["statement"], 1, 3)
    assert body["reused"] is False
    assert body["memory_ids_read"] == [] and body["memory_reads"] == []


def test_pick_never_falls_through_to_the_answer_engine(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the plan+SQL path gives no answer to a pick, the route says so; no other path runs."""
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)
    reading = _readings(_offer(dms_http))[0]
    monkeypatch.setattr(plan_sql_ask, "try_plan_sql", lambda body, *, verified: None)

    def tripwire(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a pick reached the answer engine")

    monkeypatch.setattr("CortexOS.dms.answer_engine.answer", tripwire)

    resp = _pick(dms_http, reading["reading_id"])

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == UNAVAILABLE


# -- 13, 14: offers suppressed --------------------------------------------------------


def _assert_suppressed(body: dict[str, Any], reason: str) -> None:
    assert "readings" not in body["reconfirm"], body["reconfirm"]
    assert body["answer"] == plan_sql_ask.reconfirm_answer(WHY, CLOSEST)
    assert _readings_note(body).startswith(f"offered=0 reason={reason}"), body["assumptions"]
    assert len(plan_readings.store()) == 0


def test_offer_suppressed_on_collision(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        plan_describe, "statement", lambda sql, *, keep_literal, granted: "Returns n"
    )
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)

    _assert_suppressed(_offer(dms_http), "statement-collision")


def test_offer_suppressed_on_describe_error(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(sql: str, *, keep_literal: Any, granted: Any) -> str:
        raise plan_describe.DescribeError("unknown leaf JSONPathRoot")

    monkeypatch.setattr(plan_describe, "statement", refuse)
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)

    _assert_suppressed(_offer(dms_http), "describe-failed")


@pytest.mark.parametrize("where", ["describe", "hold"])
def test_offer_suppressed_on_any_offer_exception(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("unexpected")

    if where == "describe":
        monkeypatch.setattr(plan_describe, "statement", boom)
    else:
        monkeypatch.setattr(plan_readings.ReadingStore, "hold", boom)
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)

    body = _offer(dms_http)

    assert "readings" not in body["reconfirm"]
    reason = "describe-failed" if where == "describe" else "hold-failed"
    assert _readings_note(body).startswith(f"offered=0 reason={reason}"), body["assumptions"]
    assert body["answer"] == plan_sql_ask.reconfirm_answer(WHY, CLOSEST)


def test_offer_holds_only_readings_that_pass_provenance(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The offer re-checks each reading's FreeRoute provenance before holding it."""
    real = plan_sql_ask._security_stop
    seen: list[int] = []

    def refuse_after_the_ladder(step, journal):  # noqa: ANN001
        seen.append(1)
        # Calls 1-4 are the ladder's own (three SQL writes and the reconfirm).
        if len(seen) > 4:
            return plan_sql_ask.NOT_FREEROUTE, "re-check"
        return real(step, journal)

    monkeypatch.setattr(plan_sql_ask, "_security_stop", refuse_after_the_ladder)
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)

    body = _offer(dms_http)

    assert len(seen) > 4, "the offer must re-check provenance"
    _assert_suppressed(body, "provenance")


def test_two_reading_split_offers_two(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stronger_route(armed_openvault, "", monkeypatch)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, SKU_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _offer(dms_http)

    _assert_readings_offered(body, (GOOD_SQL, SKU_SQL))
    _assert_no_values(body, (GOOD_SQL, SKU_SQL), QUESTION)


def test_single_reading_split_offers_none(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    _same_prompt_for_both_readings(monkeypatch)
    _stronger_route(armed_openvault, "", monkeypatch)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _offer(dms_http)

    assert "readings" not in body["reconfirm"]
    assert "step readings:" not in _steps(body)
    assert body["answer"] == plan_sql_ask.reconfirm_answer(
        body["reconfirm"]["why"], body["reconfirm"]["closest_question"]
    )
    assert len(plan_readings.store()) == 0


def test_pick_after_eviction_is_409(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)
    first = _readings(_offer(dms_http))[0]
    held = _held(first["reading_id"])
    for _ in range(plan_readings.MAX_HOLDS_PER_SESSION):
        plan_readings.store().hold(
            dataclasses.replace(held, reading_id=plan_readings.new_reading_id())
        )
    assert plan_readings.store().get(first["reading_id"]) is None
    spied = len(execute_spy)

    resp = _pick(dms_http, first["reading_id"])

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == UNBOUND
    assert len(execute_spy) == spied
