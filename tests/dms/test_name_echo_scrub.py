"""NAME-ECHO-CX-01: names outside the caller's grant never reach the response.

A model can write SQL that names tables and columns it invented (or that exist
but are not granted). Before this item those names came back to the caller in
answer prose, assumptions, served_reason, the reconfirm fields and HTTP error
details of ``/v1/contract/ask`` and ``/v1/contract/drillthrough``. The scrub
at those exits replaces, in free-text fields only, every identifier this
request's SQL attempts referenced (or an error quoted) that the grant does not
cover. Rows, ``sql_used``, ``contributing_sources`` and text built from rows
are never changed, and the caller's own question words are kept.

Assertions are on the envelope DMS receives: the whole JSON body, the rendered
answer text and rows, checked against an oracle run on the test lake
(CLAUDE.md section 8). The model is the scripted OpenVault.
"""

# ruff: noqa: F811  (pytest fixtures are imported, then requested by name)
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import duckdb
import pytest

from CortexOS.dms import plan_sql_ask
from tests.dms.test_c7_02_manifest_before_explain import _badge, dms_http  # noqa: F401
from tests.dms.test_l2_freeroute_envelope import (  # noqa: F401
    _l2_real_port,
    _reply_with_served_stamp,
)
from tests.test_plan_sql.test_c_loop_a_plan_sql import (  # noqa: F401
    GOOD_SQL,
    GRANT,
    SESSION,
    _ask,
    _assert_rung,
    _json,
    _plan_sql_on,
    _reply,
    _stronger_route,
)

# Names no grant in these tests covers. Each is checked on the whole body.
VAULT = "zz_payroll_vault"
BAND = "zz_salary_band"
GHOST = "zz_ghost_col"
INVENTED = (VAULT, BAND, GHOST)
PROVIDER = "groq"
MODEL = "openai/gpt-oss-120b"


def _norm(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)):
        return round(float(value), 6)
    return str(value)


def _rowset(rows: list[dict[str, Any]]) -> list[tuple[tuple[str, Any], ...]]:
    return sorted(
        (tuple(sorted((str(k), _norm(v)) for k, v in row.items())) for row in rows),
        key=repr,
    )


def _oracle(sql: str) -> list[dict[str, Any]]:
    """Run ``sql`` on the test lake directly, outside Cortex's executor."""
    from CortexOS.dms.warehouse_db import DEFAULT_DB

    con = duckdb.connect(str(DEFAULT_DB), read_only=True)
    try:
        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def _assert_no_plan_prose(body: dict[str, Any]) -> None:
    """GEN-GRANTED-CATALOG-01: one model call per attempt, so no plan lines."""
    assert not any(a.startswith("plan:") for a in body["assumptions"]), body["assumptions"]


def _assert_granted_table_in_step_prose(body: dict[str, Any], table: str) -> None:
    """A granted name in Cortex's step prose is kept, not withheld."""
    for step in ("step grant:", "step payload:"):
        line = next(a for a in body["assumptions"] if a.startswith(step))
        assert f"tables=['{table}']" in line, line


def _assert_no_invented(payload: Any) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    for name in INVENTED:
        assert name not in text, (name, text)
        assert name.upper() not in text, (name, text)


# -- plan-SQL ladder exits ------------------------------------------------------


def test_direct_abstain_on_an_invented_table_names_neither_table_nor_column(
    armed_openvault, dms_http
) -> None:
    _reply(armed_openvault, f"SELECT {BAND} FROM {VAULT}")
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_no_invented(body)
    assert _badge(body).lower() == "abstain", body
    assert body["rows"] in ([], None)
    assert body["sql_used"] is None
    assert body["drillthrough_token"] is None
    # The refusal is still named; only the identifiers are withheld.
    assert body["answer"].startswith("Not answered: PLAN_SQL_GATE_REFUSED"), body["answer"]
    assert "UNKNOWN_TABLE:" in body["answer"]
    assert "UNKNOWN_TABLE:" in (body["served_reason"] or "")
    assert any("UNKNOWN_TABLE:" in line for line in body["assumptions"])
    assert "UNKNOWN_TABLE:" in (body["provenance"]["assumptions"] or "")


def test_reconfirm_after_invented_columns_withholds_them(armed_openvault, dms_http) -> None:
    """No stronger route: the step lines name each refused write's column."""
    bad = f"SELECT {GHOST}, COUNT(*) AS n FROM transactions GROUP BY {GHOST}"
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_no_invented(body)
    assert body["answer"].startswith("Reconfirm:"), body["answer"]
    assert body["reconfirm"] is not None
    assert body["reconfirm"]["why"]
    assert body["reconfirm"]["closest_question"]
    gate = [a for a in body["assumptions"] if a.startswith("step gate:")]
    assert len(gate) == 2 and all("UNKNOWN_COLUMN:" in line for line in gate), gate
    assert body["rows"] in ([], None)
    assert body["sql_used"] is None
    _assert_rung(body, plan_sql_ask.RUNG_RECONFIRM)


def test_reconfirm_text_from_the_stronger_model_withholds_invented_names(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = "openai/gpt-oss-20b"
    _stronger_route(armed_openvault, configured, monkeypatch)
    bad = f"SELECT {GHOST} FROM transactions"
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, f"SELECT {BAND} FROM transactions", model=configured)
    _reply(
        armed_openvault,
        f"WHY: transactions has no {GHOST} or {BAND} column\n"
        "CLOSEST: how many transactions of each txn_type",
        model=configured,
    )
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_no_invented(body)
    assert body["answer"].startswith("Reconfirm:"), body["answer"]
    why = body["reconfirm"]["why"]
    # Granted names in the model's prose are kept.
    assert why.startswith("transactions has no "), why
    assert why.endswith(" column"), why
    assert body["reconfirm"]["closest_question"] == "how many transactions of each txn_type"
    assert body["served_model"] == configured
    _assert_rung(body, plan_sql_ask.RUNG_RECONFIRM)


def test_served_answer_after_an_invented_column_keeps_rows_sql_and_granted_names(
    armed_openvault, dms_http
) -> None:
    _reply(armed_openvault, f"SELECT {GHOST}, COUNT(*) AS n FROM transactions GROUP BY 1")
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)
    oracle = _oracle(GOOD_SQL)
    assert oracle

    body = _json(_ask(dms_http))

    _assert_no_invented(body)
    assert body["sql_used"].startswith(GOOD_SQL)
    assert _rowset(body["rows"]) == _rowset(oracle)
    assert _rowset(_oracle(body["sql_used"])) == _rowset(oracle)
    assert body["row_count"] == len(oracle)
    for row in oracle:
        assert f"txn_type={row['txn_type']}, n={row['n']}" in body["answer"]
    assert [s["ref_id"] for s in body["contributing_sources"]] == ["transactions"]
    assert body["drillthrough_token"]
    # One model call per attempt (GEN-GRANTED-CATALOG-01) writes no plan prose.
    # Granted names stay in the step prose around the withheld one.
    _assert_no_plan_prose(body)
    _assert_granted_table_in_step_prose(body, "transactions")
    gate = [a for a in body["assumptions"] if a.startswith("step gate:")]
    assert any("UNKNOWN_COLUMN:" in line for line in gate), gate
    _assert_rung(body, plan_sql_ask.rung_retry(1))


# -- L2 exit (no DMS payload) ---------------------------------------------------


def test_l2_abstain_on_invented_names_withholds_them(armed_openvault, dms_http) -> None:
    for _ in range(3):
        _reply_with_served_stamp(
            armed_openvault,
            f"SELECT {BAND} FROM {VAULT} ORDER BY {GHOST} LIMIT 5",
            provider=PROVIDER,
            model=MODEL,
        )
    dms_http.bind_session("name-echo-l2", {"inventory": "TRUE"})

    resp = dms_http.post(
        "/v1/contract/ask",
        json={"question": "list some skus from inventory stock", "session_id": "name-echo-l2"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_no_invented(body)
    assert _badge(body).lower() == "abstain", body
    assert body.get("rows") in ([], None)
    assert body.get("sql_used") is None
    assert "UNKNOWN_TABLE:" in body["answer"], body["answer"]


def test_l2_served_answer_is_unchanged_and_keeps_granted_names(armed_openvault, dms_http) -> None:
    """Positive control: a granted L2 answer passes the boundary untouched."""
    sql = "SELECT sku FROM inventory ORDER BY sku LIMIT 5"
    _reply_with_served_stamp(armed_openvault, sql, provider=PROVIDER, model=MODEL)
    dms_http.bind_session("name-echo-l2-ok", {"inventory": "TRUE"})
    oracle = _oracle(sql)

    resp = dms_http.post(
        "/v1/contract/ask",
        json={"question": "list some skus from inventory stock", "session_id": "name-echo-l2-ok"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provenance"]["layer"] == "generated", body
    assert _rowset(body["rows"]) == _rowset(oracle)
    assert _rowset(_oracle(body["sql_used"])) == _rowset(oracle)
    for row in oracle:
        assert str(row["sku"]) in body["answer"]
    assert [s["ref_id"] for s in body["contributing_sources"]] == ["inventory"]
    assert any("inventory" in line for line in body["assumptions"]), body["assumptions"]
    assert "[name withheld]" not in json.dumps(body)


# -- HTTP error exits -------------------------------------------------------------


def test_ask_http_error_detail_withholds_names_from_the_refused_sql(
    dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ManifestError escaping the engine becomes the 403 detail; its names are withheld."""
    from CortexOS.dms import answer_engine
    from CortexOS.execution.submit import execute_sql

    def engine(question: str, *, verified: Any, **_: Any) -> dict[str, Any]:
        # Stands in for an engine path that runs model SQL and lets the
        # executor's refusal propagate to the route.
        execute_sql(verified, f"SELECT {BAND} FROM {VAULT}")
        raise AssertionError("the executor must refuse an ungranted table")

    monkeypatch.setattr(answer_engine, "answer", engine)
    dms_http.bind_session("name-echo-http", GRANT)

    resp = dms_http.post(
        "/v1/contract/ask",
        json={"question": "how many rows are there", "session_id": "name-echo-http"},
    )

    assert resp.status_code == 403, resp.text
    _assert_no_invented(resp.text)
    detail = resp.json()["detail"]
    assert detail["code"]
    assert "not named by this manifest" in detail["message"], detail


def test_drillthrough_error_detail_withholds_names_from_the_refused_sql(
    dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.execution import drillthrough
    from CortexOS.execution.manifest import ManifestError
    from CortexOS.execution.session_manifests import get_session_registry
    from CortexOS.execution.submit import execute_sql

    dms_http.bind_session("name-echo-drill", GRANT)

    def drill(token: str, **_: Any) -> dict[str, Any]:
        del token
        verified = get_session_registry().resolve("name-echo-drill")
        try:
            execute_sql(verified, f"SELECT {BAND} FROM {VAULT}")
        except ManifestError as exc:
            raise drillthrough.DrillthroughError(f"drill refused: {exc}") from exc
        raise AssertionError("the executor must refuse an ungranted table")

    monkeypatch.setattr(drillthrough, "execute_drillthrough", drill)

    resp = dms_http.post("/v1/contract/drillthrough", json={"token": "dt_x.y"})

    assert resp.status_code == 400, resp.text
    _assert_no_invented(resp.text)
    assert resp.json()["detail"]["message"].startswith("drill refused: ")


def test_drillthrough_success_is_unchanged(
    dms_http, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: the drill rows and SQL are what the executor returned.

    The demo lake has no provenance columns, so this drill runs on a small lake
    that has them (the drill rewrite projects ``<table>._src*``).
    """
    from CortexOS.execution import submit
    from CortexOS.execution.drillthrough import (
        execute_drillthrough,
        manifest_content_hash,
        mint_token,
    )
    from CortexOS.execution.session_manifests import get_session_registry

    lake = tmp_path / "drill.duckdb"
    con = duckdb.connect(str(lake))
    con.execute(
        "CREATE TABLE stock AS SELECT * FROM (VALUES "
        "('SKU-A', 3, 'r1', 1, 'i1', 'f.csv'), ('SKU-A', 4, 'r2', 2, 'i1', 'f.csv'), "
        "('SKU-B', 5, 'r3', 3, 'i1', 'f.csv')) "
        "AS v(sku, qty, _src_ref_id, _src_row, _ingest_id, _src)"
    )
    con.close()
    monkeypatch.setattr(submit, "DEFAULT_DB", lake)
    dms_http.bind_session("name-echo-drill-ok", {"stock": "TRUE"})
    verified = get_session_registry().resolve("name-echo-drill-ok")
    sql = "SELECT sku, SUM(qty) AS total FROM stock GROUP BY sku"
    token = mint_token(
        answer_id="a-1",
        session_id="name-echo-drill-ok",
        manifest_hash=manifest_content_hash(verified.manifest),
        sql=sql,
    )

    resp = dms_http.post("/v1/contract/drillthrough", json={"token": token})

    assert resp.status_code == 200, resp.text
    drilled = resp.json()
    direct = execute_drillthrough(token)
    assert drilled["sql_used"] == direct["sql_used"]
    assert drilled["rows"] == json.loads(json.dumps(direct["rows"]))
    assert drilled["row_count"] == direct["row_count"] == 3
    assert drilled["total_count"] == direct["total_count"]
    assert {row["stock__src_ref_id"] for row in drilled["rows"]} == {"r1", "r2", "r3"}


def test_granted_plan_sql_answer_passes_the_boundary_untouched(armed_openvault, dms_http) -> None:
    """Positive control: no invented name, so nothing is withheld."""
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)
    oracle = _oracle(GOOD_SQL)

    body = _json(_ask(dms_http))

    assert body["sql_used"].startswith(GOOD_SQL)
    assert _rowset(body["rows"]) == _rowset(oracle)
    for row in oracle:
        assert f"txn_type={row['txn_type']}, n={row['n']}" in body["answer"]
    assert [s["ref_id"] for s in body["contributing_sources"]] == ["transactions"]
    _assert_no_plan_prose(body)
    _assert_granted_table_in_step_prose(body, "transactions")
    assert "[name withheld]" not in json.dumps(body)
    _assert_rung(body, plan_sql_ask.RUNG_PLAN)


# -- reconfirm: Yes must run a whole question -----------------------------------


def test_withheld_word_in_closest_question_is_replaced_by_a_question_built_from_the_grant(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused attempts invent the columns ``total`` and ``date``; the model's closest
    question uses both words. Yes resends ``closest_question`` as the question, so it
    must never carry a withheld marker: Cortex builds one from the grant instead, and
    Yes on it runs and returns the oracle rows."""
    configured = "openai/gpt-oss-20b"
    _stronger_route(armed_openvault, configured, monkeypatch)
    _reply(armed_openvault, "SELECT txn_type, total FROM transactions")
    _reply(armed_openvault, "SELECT txn_type, total FROM transactions")
    _reply(armed_openvault, "SELECT txn_type, date FROM transactions", model=configured)
    _reply(
        armed_openvault,
        "WHY: transactions has no stored per-type figure\n"
        "CLOSEST: What is the total quantity_kg of transactions for each txn_type by date?",
        model=configured,
    )
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["answer"].startswith("Reconfirm:"), body["answer"]
    closest = body["reconfirm"]["closest_question"]
    assert closest == "What are the distinct txn_type values in transactions?", closest
    assert body["reconfirm"]["why"] == "transactions has no stored per-type figure"
    assert body["answer"] == (
        "Reconfirm: transactions has no stored per-type figure "
        f"Closest question: {closest} Yes runs that question. "
        f"No answers '{plan_sql_ask.NOT_FOUND_ANSWER}'."
    )
    assert body["rows"] in ([], None)
    assert body["sql_used"] is None
    _assert_rung(body, plan_sql_ask.RUNG_RECONFIRM)

    # Yes: the closest question is resent as the question and answered from the lake.
    yes_sql = "SELECT DISTINCT txn_type FROM transactions ORDER BY txn_type"
    _reply(armed_openvault, yes_sql)
    oracle = _oracle(yes_sql)
    assert oracle

    yes = _json(_ask(dms_http, question=closest, plan_sql_confirm="yes"))

    assert yes["reconfirm"] is None
    assert yes["sql_used"].startswith(yes_sql)
    assert _rowset(yes["rows"]) == _rowset(oracle)
    assert _rowset(_oracle(yes["sql_used"])) == _rowset(oracle)
    for row in oracle:
        assert str(row["txn_type"]) in yes["answer"], yes["answer"]
    assert any(line.startswith("step confirm:") for line in yes["assumptions"])
    assert "[name withheld]" not in json.dumps(yes)


def test_reconfirm_answer_keeps_cortex_wording_when_a_template_word_is_withheld(
    armed_openvault, dms_http
) -> None:
    """Refused attempts invent the columns ``question`` and ``found``. Those words in
    Cortex's own reconfirm sentence are not names from the SQL and stay as written."""
    bad = "SELECT question, found FROM transactions"
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    closest = body["reconfirm"]["closest_question"]
    why = body["reconfirm"]["why"]
    assert "[name withheld]" not in closest, closest
    assert body["answer"] == (
        f"Reconfirm: {why} Closest question: {closest} Yes runs that question. "
        f"No answers '{plan_sql_ask.NOT_FOUND_ANSWER}'."
    )
    # The refused names themselves stay withheld in the free text around them.
    gate = [a for a in body["assumptions"] if a.startswith("step gate:")]
    assert len(gate) == 2, gate
    for line in gate:
        assert "UNKNOWN_COLUMN:question" not in line and "UNKNOWN_COLUMN:found" not in line
    _assert_rung(body, plan_sql_ask.RUNG_RECONFIRM)


# -- PICK-READING-01: the statements of offered readings ---------------------------


def _split_after_invented(fake, monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """A refused attempt names an invented column, then three readings disagree."""
    from CortexOS.dms import plan_sql_vote
    from CortexOS.plan_sql import readings as plan_readings
    from tests.test_plan_sql.test_plan_sql_vote import KG_SQL, SKU_SQL

    monkeypatch.setenv(plan_sql_vote.ENABLED_ENV, "1")
    monkeypatch.setenv(plan_readings.READINGS_ENV, "1")
    plan_readings.reset_for_tests()
    configured = "openai/gpt-oss-20b"
    _stronger_route(fake, configured, monkeypatch)
    _reply(fake, bad)
    _reply(fake, GOOD_SQL)
    _reply(fake, SKU_SQL)
    _reply(fake, KG_SQL, model=configured)
    _reply(
        fake,
        "WHY: the count is unclear\nCLOSEST: how many transactions of each txn_type",
        model=configured,
    )


def _held_readings(body: dict[str, Any]) -> list[Any]:
    from CortexOS.plan_sql import readings as plan_readings

    return [plan_readings.store().get(r["reading_id"]) for r in body["reconfirm"]["readings"]]


def test_reading_statements_withhold_invented_names(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A statement naming an invented column is scrubbed like any free text, and the
    reconfirm answer is rendered again from the scrubbed statements."""
    from cortex_contract.answer import PlanSqlReading

    from CortexOS.plan_sql import describe

    _split_after_invented(armed_openvault, monkeypatch, f"SELECT {GHOST} FROM transactions")
    counter = iter(range(1, 10))
    monkeypatch.setattr(
        describe,
        "statement",
        lambda sql, *, keep_literal, granted: f"Returns {GHOST} reading {next(counter)}",
    )
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_no_invented(body)
    readings = body["reconfirm"]["readings"]
    assert [r["statement"] for r in readings] == [
        f"Returns [name withheld] reading {i}" for i in (1, 2, 3)
    ]
    held = _held_readings(body)
    assert [r["reading_id"] for r in readings] == [h.reading_id for h in held]
    assert [r["row_count"] for r in readings] == [h.row_count for h in held]
    for reading in readings:
        PlanSqlReading.model_validate(reading)
    assert body["answer"] == plan_sql_ask.reconfirm_answer(
        body["reconfirm"]["why"],
        body["reconfirm"]["closest_question"],
        [(r["statement"], r["row_count"]) for r in readings],
    )
    assert body["rows"] in ([], None) and body["sql_used"] is None
    _assert_rung(body, plan_sql_ask.RUNG_RECONFIRM)


def test_a_scrubbed_statement_still_fits_the_published_length(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[name withheld]`` is longer than a one-character name: a statement full of
    them is capped again, so the wire never breaks the published maxLength 320."""
    from cortex_contract.answer import PlanSqlReading

    from CortexOS.plan_sql import describe

    _split_after_invented(armed_openvault, monkeypatch, "SELECT q FROM transactions")
    counter = iter(range(1, 10))
    monkeypatch.setattr(
        describe,
        "statement",
        lambda sql, *, keep_literal, granted: f"r{next(counter)} " + " ".join(["q"] * 148),
    )
    dms_http.bind_session(SESSION, GRANT)

    resp = _ask(dms_http)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    readings = body["reconfirm"]["readings"]
    assert len(readings) == 3
    for i, reading in enumerate(readings, 1):
        assert len(reading["statement"]) <= 320
        assert reading["statement"].startswith(f"r{i} [name withheld]")
        assert reading["statement"].endswith(describe.SHORTENED)
        PlanSqlReading.model_validate(reading)
    assert body["answer"] == plan_sql_ask.reconfirm_answer(
        body["reconfirm"]["why"],
        body["reconfirm"]["closest_question"],
        [(r["statement"], r["row_count"]) for r in readings],
    )


# -- every form a name can take in model SQL --------------------------------------
#
# The review of 13fa80f found invented names that the scrub did not collect
# because they were not a plain table or column identifier: a 4-part table name
# (sqlglot puts the last two parts in a Dot), a function in relation position
# (FROM f(), JOIN f(), LATERAL f()) and its schema-qualified spelling. The cases
# below cover those and the other spellings the model's SQL can carry a name in.
# Each one is driven through both AI exits of /v1/contract/ask and checked on the
# whole JSON body. ``{T}`` and ``{C}`` are a granted table and one of its columns.

CATALOG = "zz_vault_catalog"
SCHEMA = "zz_vault_schema"

NAME_FORMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "four_part_table": (f"SELECT * FROM {CATALOG}.{SCHEMA}.{VAULT}.{BAND}", (CATALOG, SCHEMA)),
    "three_part_table": (f"SELECT {{C}} FROM {CATALOG}.{SCHEMA}.{VAULT}", (CATALOG, SCHEMA)),
    "table_function": (f"SELECT * FROM {VAULT}()", ()),
    "table_function_with_argument": (f"SELECT * FROM {VAULT}('{{C}}')", ()),
    "schema_qualified_table_function": (f"SELECT * FROM {SCHEMA}.{VAULT}()", (SCHEMA,)),
    "join_table_function": (f"SELECT {{C}} FROM {{T}} JOIN {VAULT}() ON TRUE", ()),
    "lateral_table_function": (f"SELECT {{C}} FROM {{T}}, LATERAL {VAULT}({{T}}.{{C}})", ()),
    "scalar_function": (f"SELECT {VAULT}({{C}}) FROM {{T}}", ()),
    "schema_qualified_scalar_function": (f"SELECT {SCHEMA}.{VAULT}({{C}}) FROM {{T}}", (SCHEMA,)),
    "five_part_column": (
        f"SELECT {CATALOG}.{SCHEMA}.{VAULT}.{BAND}.{GHOST} FROM {{T}}",
        (CATALOG, SCHEMA),
    ),
    "using_column": (f"SELECT {{C}} FROM {{T}} JOIN {{T}} AS other USING ({GHOST})", ()),
    "user_defined_type": (f"SELECT CAST({{C}} AS {BAND}) FROM {{T}}", ()),
    "quoted_table": (f'SELECT {{C}} FROM "{VAULT}"', ()),
    "subquery_table": (f"SELECT {{C}} FROM {{T}} WHERE {{C}} IN (SELECT {GHOST} FROM {VAULT})", ()),
}


def _form_sql(form: str, table: str, column: str) -> str:
    return NAME_FORMS[form][0].replace("{T}", table).replace("{C}", column)


def _assert_none_of(payload: Any, names: tuple[str, ...]) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    for name in (*INVENTED, *names):
        assert name not in text, (name, text)
        assert name.upper() not in text, (name, text)


@pytest.mark.parametrize("form", sorted(NAME_FORMS))
def test_plan_sql_exit_withholds_every_name_form(form: str, armed_openvault, dms_http) -> None:
    bad = _form_sql(form, "transactions", "txn_type")
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_none_of(body, NAME_FORMS[form][1])
    assert body["rows"] in ([], None), body
    assert body["sql_used"] is None, body
    assert body["drillthrough_token"] is None, body
    assert body["answer"].startswith(("Not answered:", "Reconfirm:")), body["answer"]
    if body["answer"].startswith("Reconfirm:"):
        assert "[name withheld]" not in body["reconfirm"]["closest_question"], body["reconfirm"]


@pytest.mark.parametrize("form", sorted(NAME_FORMS))
def test_l2_exit_withholds_every_name_form(form: str, armed_openvault, dms_http) -> None:
    bad = _form_sql(form, "inventory", "sku")
    for _ in range(3):
        _reply_with_served_stamp(armed_openvault, bad, provider=PROVIDER, model=MODEL)
    session = f"name-echo-form-{form}"
    dms_http.bind_session(session, {"inventory": "TRUE"})

    resp = dms_http.post(
        "/v1/contract/ask",
        json={"question": "list some skus from inventory stock", "session_id": session},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_none_of(body, NAME_FORMS[form][1])
    assert _badge(body).lower() == "abstain", body
    assert body.get("rows") in ([], None), body
    assert body.get("sql_used") is None, body


def test_served_answer_keeps_engine_function_and_default_schema_names(
    armed_openvault, dms_http
) -> None:
    """Positive control for the wider extraction.

    A refused attempt invents a column, so the scrub runs on the served answer.
    The served SQL calls a built-in sqlglot does not know (``strip_accents``)
    and reads the granted table through DuckDB's default schema
    (``main.transactions``). Neither name is outside the grant: ``sql_used``
    keeps both, the step prose keeps the granted table, and the rows are the
    oracle rows. One model call per attempt writes no plan prose.
    """
    good = (
        "SELECT strip_accents(txn_type) AS kind, COUNT(*) AS n "
        "FROM main.transactions GROUP BY 1 ORDER BY n DESC"
    )
    _reply(armed_openvault, f"SELECT {GHOST}, COUNT(*) AS n FROM transactions GROUP BY 1")
    _reply(armed_openvault, good)
    dms_http.bind_session(SESSION, GRANT)
    oracle = _oracle(good)
    assert oracle

    body = _json(_ask(dms_http))

    _assert_no_invented(body)
    # The gate renders the call upper-case (STRIP_ACCENTS); the statement is the same.
    assert body["sql_used"].lower().startswith(good.lower()), body["sql_used"]
    assert _rowset(body["rows"]) == _rowset(oracle)
    assert _rowset(_oracle(body["sql_used"])) == _rowset(oracle)
    for row in oracle:
        assert f"kind={row['kind']}, n={row['n']}" in body["answer"], body["answer"]
    assert [s["ref_id"] for s in body["contributing_sources"]] == ["transactions"]
    assert "main.transactions" in body["sql_used"], body["sql_used"]
    assert "STRIP_ACCENTS(txn_type)" in body["sql_used"], body["sql_used"]
    assert "[name withheld]" not in body["answer"], body["answer"]
    _assert_no_plan_prose(body)
    _assert_granted_table_in_step_prose(body, "transactions")
    gate = [a for a in body["assumptions"] if a.startswith("step gate:")]
    assert any("UNKNOWN_COLUMN:[name withheld]" in line for line in gate), gate


# -- review of 96f0bb8 ------------------------------------------------------------
#
# (1) A bare grant key grants the table in DuckDB's default schema only (the
# manifest's ``_grant_key``). The columns of a same-named table in another
# schema are real names outside the grant and must be withheld like invented
# ones. (2) A name longer than 128 characters is withheld like any other.

HR_SECRET = "zz_hr_secret_col"


@pytest.fixture
def hr_lake(dms_http, tmp_path, monkeypatch: pytest.MonkeyPatch):
    """The test lake plus ``hr.transactions`` and ``hr.inventory``, which share the
    granted tables' names but are in schema ``hr``, so no grant here covers them."""
    import shutil

    from CortexOS.execution import submit
    from CortexOS.execution.warehouse import warehouse_path

    source = warehouse_path(submit.DEFAULT_DB)
    lake = tmp_path / "hr_lake.duckdb"
    shutil.copyfile(source, lake)
    wal = source.with_name(source.name + ".wal")
    if wal.exists():
        shutil.copyfile(wal, lake.with_name(lake.name + ".wal"))
    con = duckdb.connect(str(lake))
    con.execute("CREATE SCHEMA hr")
    con.execute(f"CREATE TABLE hr.transactions ({HR_SECRET} INTEGER, txn_type VARCHAR)")
    con.execute(f"CREATE TABLE hr.inventory ({HR_SECRET} INTEGER, sku VARCHAR)")
    con.close()
    monkeypatch.setattr(submit, "DEFAULT_DB", lake)
    return lake


def _assert_absent(body: Any, *names: str) -> None:
    text = json.dumps(body)
    for name in names:
        assert name not in text, (name, text)
        assert name.upper() not in text, (name, text)
        assert name.lower() not in text.lower(), (name, text)


@pytest.mark.parametrize("schema", ["hr.", ""])
def test_plan_sql_exit_withholds_a_column_of_a_same_named_ungranted_table(
    schema: str, armed_openvault, dms_http, hr_lake
) -> None:
    bad = f"SELECT {HR_SECRET} FROM {schema}transactions"
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_absent(body, HR_SECRET)
    assert body["rows"] in ([], None), body
    assert body["sql_used"] is None, body
    assert body["answer"].startswith(("Not answered:", "Reconfirm:")), body["answer"]


@pytest.mark.parametrize("schema", ["hr.", ""])
def test_l2_exit_withholds_a_column_of_a_same_named_ungranted_table(
    schema: str, armed_openvault, dms_http, hr_lake
) -> None:
    bad = f"SELECT {HR_SECRET} FROM {schema}inventory LIMIT 5"
    for _ in range(3):
        _reply_with_served_stamp(armed_openvault, bad, provider=PROVIDER, model=MODEL)
    session = f"name-echo-hr-{schema or 'bare'}"
    dms_http.bind_session(session, {"inventory": "TRUE"})

    resp = dms_http.post(
        "/v1/contract/ask",
        json={"question": "list some skus from inventory stock", "session_id": session},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_absent(body, HR_SECRET)
    assert _badge(body).lower() == "abstain", body
    assert body.get("rows") in ([], None), body
    assert body.get("sql_used") is None, body


def test_served_answer_with_a_same_named_table_elsewhere_keeps_granted_names(
    armed_openvault, dms_http, hr_lake
) -> None:
    """Positive control on the lake with ``hr.transactions``: the granted columns
    of ``main.transactions`` stay in the prose, rows are the oracle rows."""
    _reply(armed_openvault, f"SELECT {HR_SECRET}, COUNT(*) AS n FROM transactions GROUP BY 1")
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)
    oracle = _oracle(GOOD_SQL)
    assert oracle

    body = _json(_ask(dms_http))

    _assert_absent(body, HR_SECRET)
    assert body["sql_used"].startswith(GOOD_SQL)
    assert _rowset(body["rows"]) == _rowset(oracle)
    assert _rowset(_oracle(body["sql_used"])) == _rowset(oracle)
    for row in oracle:
        assert f"txn_type={row['txn_type']}, n={row['n']}" in body["answer"]
    assert [s["ref_id"] for s in body["contributing_sources"]] == ["transactions"]
    _assert_no_plan_prose(body)
    _assert_granted_table_in_step_prose(body, "transactions")
    gate = [a for a in body["assumptions"] if a.startswith("step gate:")]
    assert any("UNKNOWN_COLUMN:[name withheld]" in line for line in gate), gate


LONG_VAULT = "zz_payroll_vault_" + "x" * 120
LONG_COLUMN = "zz_ghost_col_" + "y" * 200


@pytest.mark.parametrize(
    "template",
    [
        "SELECT {C} FROM " + LONG_VAULT,
        "SELECT " + LONG_COLUMN + " FROM {T}",
        'SELECT {C} FROM "' + LONG_VAULT + '"',
    ],
    ids=["long_table", "long_column", "long_quoted_table"],
)
def test_plan_sql_exit_withholds_a_name_longer_than_128_characters(
    template: str, armed_openvault, dms_http
) -> None:
    bad = template.replace("{T}", "transactions").replace("{C}", "txn_type")
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_absent(body, LONG_VAULT, LONG_COLUMN)
    assert body["rows"] in ([], None), body
    assert body["sql_used"] is None, body
    assert body["answer"].startswith(("Not answered:", "Reconfirm:")), body["answer"]


@pytest.mark.parametrize(
    "template",
    [
        "SELECT {C} FROM " + LONG_VAULT,
        "SELECT " + LONG_COLUMN + " FROM {T}",
        'SELECT {C} FROM "' + LONG_VAULT + '"',
    ],
    ids=["long_table", "long_column", "long_quoted_table"],
)
def test_l2_exit_withholds_a_name_longer_than_128_characters(
    template: str, armed_openvault, dms_http
) -> None:
    bad = template.replace("{T}", "inventory").replace("{C}", "sku")
    for _ in range(3):
        _reply_with_served_stamp(armed_openvault, bad, provider=PROVIDER, model=MODEL)
    session = "name-echo-long"
    dms_http.bind_session(session, {"inventory": "TRUE"})

    resp = dms_http.post(
        "/v1/contract/ask",
        json={"question": "list some skus from inventory stock", "session_id": session},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_absent(body, LONG_VAULT, LONG_COLUMN)
    assert _badge(body).lower() == "abstain", body
    assert body.get("rows") in ([], None), body
    assert body.get("sql_used") is None, body
