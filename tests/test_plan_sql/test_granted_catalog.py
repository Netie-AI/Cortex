"""GEN-GRANTED-CATALOG-01 must-fails.

Red on the C-LOOP-A head: the prompt is the full payload, an unknown-column
retry has no catalog hint, and the first attempt is a plan call plus a SQL
call. Green here: the prompt is the grant intersected with
``AskRequest.schema_context`` (contract 1.7.0), the retry carries a
did-you-mean hint, and each attempt is one FreeRoute call.

``schema_context`` is read by ``CortexOS.insights.schema_context`` only, the
parser and rule ``POST /v1/insights`` uses. The review of 017e88f found this
PR's own parser filed a DMS table head (``- t reason=... description=...``)
as a column of the table above it, so an ungranted table's columns reached
the prompt. Those shapes are pinned below.

The review of f2a26a3 found the did-you-mean hint offered columns that are
not close (``revenue`` gave ``txn_type, sku, quantity_kg``, and when nothing
was close a fallback named the first three catalog columns anyway). A hint now
names only a typo of the unknown column from the granted catalog, or nothing.
Security's ruling on the same head: no sampled value of a masked column reaches
a model prompt, and a column whose mask state is unknown counts as masked; a
schema type token is a SQL type word, never a table or column name.

Answer-path tests assert the badge, the rows against an oracle run on the
test lake, and the rendered answer text (CLAUDE.md section 8), as well as the
prompt. Names in this file are synthetic except the test lake's tables.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from CortexOS.dms import plan_sql_ask
from CortexOS.insights import schema_context as schema_ctx
from CortexOS.memory import space_memory as sm
from CortexOS.plan_sql import catalog as plan_catalog
from CortexOS.plan_sql.payload import PlanSqlRequest, schema_block
from tests.dms.test_c7_02_manifest_before_explain import _badge, dms_http  # noqa: F401
from tests.dms.test_name_echo_scrub import _oracle, _rowset
from tests.dms.test_schema_context_dms_shape import _load
from tests.test_plan_sql.test_c_loop_a_plan_sql import (
    GOOD_SQL,
    GRANT,
    PROVIDER,
    QUESTION,
    SESSION,
    _ask,
    _json,
    _ran,
    _reply,
    _route_rows,
    _stronger_route,
)
from tests.test_plan_sql.test_c_loop_a_plan_sql import PAYLOAD as UNMASKED_PAYLOAD

GHOST = "ghost_col_zz"
UNGRANTED_TABLE = "other_ledger"
UNGRANTED_COLUMN = "secret_col"
UNGRANTED_WORDS = "ledger of other books"
SCHEMA_CONTEXT = "\n".join(
    [
        "DIALECT: generic",
        "SCHEMA",
        "- transactions reason=score:1",
        "- txn_type VARCHAR reason=kept",
        f"- {UNGRANTED_TABLE}",
        f"- {UNGRANTED_COLUMN} INTEGER",
        "JOINS",
        f"- {UNGRANTED_TABLE}.{UNGRANTED_COLUMN} = transactions.txn_type (linked)",
    ]
)
# The head shape the DMS renderer writes: reason=, then description= to the
# end of the line. The parser this PR used to carry read it as a column of
# ``transactions``, and ``secret_col`` after it as another one.
DMS_SHAPED_SCHEMA_CONTEXT = "\n".join(
    [
        "DIALECT: generic",
        "SCHEMA",
        "- transactions reason=score:1",
        "- txn_type VARCHAR reason=kept",
        f"- {UNGRANTED_TABLE} reason=score:0.2 description={UNGRANTED_WORDS}",
        f"- {UNGRANTED_COLUMN} INTEGER",
    ]
)
GRANTED_ONLY_PROMPT_LINE = "- transactions(txn_type VARCHAR)"


@pytest.fixture(autouse=True)
def _plan_sql_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(plan_sql_ask.ENABLED_ENV, "1")
    monkeypatch.delenv(sm.ENABLED_ENV, raising=False)
    monkeypatch.delenv(sm.SCORED_ROUND_ENV, raising=False)
    sm.reset_space_memory_for_tests()
    yield
    sm.reset_space_memory_for_tests()


PAYLOAD: dict[str, Any] = {
    "tables": [
        {
            "name": "transactions",
            "columns": [
                {"name": "txn_type", "type": "VARCHAR"},
                {"name": GHOST, "type": "VARCHAR", "description": "outside the granted catalog"},
            ],
        }
    ]
}


def _prompts(fake) -> list[str]:
    return [call["body"]["messages"][1]["content"] for call in fake.chat_calls]


def _assert_served(body: dict[str, Any], sql: str, rung: str) -> list[dict[str, Any]]:
    """The envelope DMS receives: badge, exact rows, rendered text, SQL, rung."""
    oracle = _oracle(sql)
    assert oracle
    assert _badge(body).lower() == "session", body
    assert body["provenance"]["layer"] == "plan_sql", body
    assert body["sql_used"].startswith(sql), body["sql_used"]
    assert _rowset(body["rows"]) == _rowset(oracle)
    assert _rowset(_oracle(body["sql_used"])) == _rowset(oracle)
    assert body["row_count"] == len(oracle)
    if len(oracle) == 1 and len(oracle[0]) == 1:
        ((key, value),) = oracle[0].items()
        assert body["answer"] == f"Result: {key} = {value}", body["answer"]
    else:
        assert body["answer"].startswith(f"Found {len(oracle)} row(s)."), body["answer"]
        for row in oracle:
            rendered = ", ".join(f"{key}={value}" for key, value in row.items())
            assert rendered in body["answer"], (rendered, body["answer"])
    assert body["plan_sql_rung"] == rung
    assert body["served_provider"] == PROVIDER and body["served_model"]
    assert body["drillthrough_token"]
    return oracle


def _assert_named_abstain(body: dict[str, Any], code: str) -> None:
    assert _badge(body).lower() == "abstain", body
    assert body["rows"] in ([], None)
    assert body["sql_used"] is None
    assert body["drillthrough_token"] is None
    assert body["plan_sql_rung"] is None
    assert body["answer"].startswith(f"Not answered: {code}: "), body["answer"]
    assert any(line.startswith(f"{code}: ") for line in body["assumptions"]), body["assumptions"]


# -- the prompt is grant ∩ schema_context ---------------------------------------


def test_must_fail_column_outside_granted_catalog_never_in_prompt_or_sql(
    armed_openvault,
    dms_http,  # noqa: F811
) -> None:
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, payload=PAYLOAD, schema_context=SCHEMA_CONTEXT))

    assert len(armed_openvault.chat_calls) == 1
    (prompt,) = _prompts(armed_openvault)
    assert GRANTED_ONLY_PROMPT_LINE in prompt.splitlines(), prompt
    for name in (GHOST, UNGRANTED_TABLE, UNGRANTED_COLUMN):
        assert name not in prompt, name
    # The ungranted join went with its table.
    assert "JOINS" not in prompt
    _assert_served(body, GOOD_SQL, plan_sql_ask.RUNG_PLAN)
    for name in (GHOST, UNGRANTED_TABLE, UNGRANTED_COLUMN):
        assert name not in json.dumps(body), name


def test_must_fail_dms_shaped_ungranted_head_is_dropped_not_folded_into_the_table_above(
    armed_openvault,
    dms_http,  # noqa: F811
) -> None:
    """Review repro on 017e88f: the prompt read
    ``- transactions(txn_type VARCHAR, other_ledger of, secret_col INTEGER)``."""
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, payload=PAYLOAD, schema_context=DMS_SHAPED_SCHEMA_CONTEXT))

    assert len(armed_openvault.chat_calls) == 1
    (prompt,) = _prompts(armed_openvault)
    assert GRANTED_ONLY_PROMPT_LINE in prompt.splitlines(), prompt
    for text in (UNGRANTED_TABLE, UNGRANTED_COLUMN, UNGRANTED_WORDS, "description=", GHOST):
        assert text not in prompt, text
    _assert_served(body, GOOD_SQL, plan_sql_ask.RUNG_PLAN)
    for text in (UNGRANTED_TABLE, UNGRANTED_COLUMN, UNGRANTED_WORDS):
        assert text not in json.dumps(body), text


def test_must_fail_both_granted_described_table_keeps_its_own_columns(
    armed_openvault,
    dms_http,  # noqa: F811
) -> None:
    """Review repro on 017e88f: with both tables granted, the described table
    dropped out and its columns were attributed to the table above it. The only
    SQL that could then run read the wrong table."""
    schema = "\n".join(
        [
            "DIALECT: duckdb",
            "SCHEMA",
            "- transactions reason=score:0.9 description=stock movements",
            "- txn_type VARCHAR description=IN or OUT reason=score:0.9",
            "- inventory reason=score:0.4 description=stock on hand by sku",
            "- sku VARCHAR primary_key reason=score:0.4",
            "- quantity_kg DOUBLE reason=score:0.3",
            "JOINS",
            "- transactions.sku = inventory.sku (stock_movement) reason=path:a>b",
        ]
    )
    sql = "SELECT COUNT(DISTINCT sku) AS skus FROM inventory"
    _reply(armed_openvault, sql)
    dms_http.bind_session(SESSION, {"transactions": "TRUE", "inventory": "TRUE"})

    body = _json(
        _ask(
            dms_http,
            payload=PAYLOAD,
            question="how many distinct skus are in stock",
            schema_context=schema,
        )
    )

    (prompt,) = _prompts(armed_openvault)
    lines = prompt.splitlines()
    assert "- transactions(txn_type VARCHAR, sku)" in lines, prompt
    assert "- inventory(sku VARCHAR, quantity_kg DOUBLE)" in lines, prompt
    assert "- transactions.sku = inventory.sku" in lines, prompt
    for text in ("stock on hand", "description=", "reason=", GHOST):
        assert text not in prompt, text
    oracle = _assert_served(body, sql, plan_sql_ask.RUNG_PLAN)
    assert [s["ref_id"] for s in body["contributing_sources"]] == ["inventory"]
    assert oracle[0]["skus"] > 0


def test_dms_render_narrows_to_the_granted_tables_main_parses() -> None:
    """The literal DMS CRM render: the catalog is what ``parse_schema_context``
    reads (account and contact), cut to the grant. The parser this PR used to
    carry returned no table for these bytes, so every real DMS render abstained."""
    text = _load("dms_schema_context_crm.txt")
    assert schema_ctx.problem(text) is None
    parsed = schema_ctx.parse_schema_context(text)
    payload = {"tables": [{"name": "account", "columns": [{"name": "account_id"}]}]}
    from cortex_contract.answer import AskPayload

    request = PlanSqlRequest("q", AskPayload.model_validate(payload), schema_context=text)

    both, code = plan_catalog.narrow(request, {"account", "contact"})
    assert code == ""
    assert {t.name: [c.name for c in t.columns] for t in both.payload.tables} == parsed.tables
    block = schema_block(both)
    assert "- account(account_id bigint, status varchar)" in block.splitlines(), block
    assert "- contact.account_id = account.account_id" in block.splitlines(), block
    for text_part in ("DMSMASK", "DMSVAL", "samples=", "reason=", "midmarket"):
        assert text_part not in block, text_part

    one, code = plan_catalog.narrow(request, {"account"})
    assert code == ""
    assert [t.name for t in one.payload.tables] == ["account"]
    assert one.payload.joins == []
    assert "contact" not in schema_block(one)

    none, code = plan_catalog.narrow(request, {"transactions"})
    assert code == plan_catalog.SCHEMA_CONTEXT_UNGRANTED
    assert none is request


# -- retry hint and call count --------------------------------------------------


def test_must_fail_unknown_column_retry_carries_did_you_mean(
    armed_openvault,
    dms_http,  # noqa: F811
) -> None:
    # The unknown-column attempt, then the error-fed rewrite on the same model.
    # (Main's ladder only escalates to a stronger route OpenVault configured,
    # so both writes stay inside SQL_ATTEMPTS.)
    _reply(armed_openvault, "SELECT txn_typo FROM transactions")
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    retries = [prompt for prompt in _prompts(armed_openvault) if "UNKNOWN_COLUMN" in prompt]
    assert retries
    retry = retries[0]
    assert "did you mean:" in retry
    assert "txn_type" in retry.split("did you mean:", 1)[1]
    _assert_served(body, GOOD_SQL, plan_sql_ask.rung_retry(1))
    assert "txn_typo" not in body["answer"]


def test_must_fail_one_model_call_per_attempt(
    armed_openvault,
    dms_http,  # noqa: F811
) -> None:
    """Fake OpenVault client: one chat call per generation attempt."""
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    first = _json(_ask(dms_http, question=QUESTION))

    assert len(armed_openvault.chat_calls) == 1
    assert [row[0] for row in _route_rows()] == ["plan-sql-sql"]
    _assert_served(first, GOOD_SQL, plan_sql_ask.RUNG_PLAN)
    assert first["model_calls"] == {"plan": 1, "total": 1}

    start = len(armed_openvault.chat_calls)
    routes_before = len(_route_rows())
    _reply(armed_openvault, "SELECT txn_typo FROM transactions")
    _reply(armed_openvault, GOOD_SQL)
    second = _json(_ask(dms_http, question="count the types again"))
    added = len(armed_openvault.chat_calls) - start

    _assert_served(second, GOOD_SQL, plan_sql_ask.rung_retry(1))
    assert second["model_calls"] == {"plan": 1, "error-fed-retry-1": 1, "total": 2}
    assert added == 2
    tasks = [row[0] for row in _route_rows()]
    assert tasks[routes_before:] == ["plan-sql-sql", "plan-sql-sql"]
    assert "plan-sql-plan" not in tasks


# -- did-you-mean: typos from the granted catalog, or nothing --------------------

LAKE_COLUMNS = ["txn_type", "sku", "quantity_kg"]
# The DMS CRM render's columns (tests/dms/fixtures/dms_schema_context_crm.txt).
CRM_COLUMNS = [
    "account_id",
    "status",
    "contact_id",
    "customer_name",
    "date_of_birth",
    "email",
    "memo",
    "passport_no",
    "phone",
]


@pytest.mark.parametrize(
    ("unknown", "columns"),
    [
        # Review of f2a26a3: each of these named unrelated columns.
        pytest.param("revenue", LAKE_COLUMNS, id="revenue-fallback"),
        pytest.param("amount", LAKE_COLUMNS, id="amount-fallback"),
        pytest.param("sales_qty", LAKE_COLUMNS, id="sales-qty-fallback"),
        pytest.param("segment", CRM_COLUMNS, id="segment-gave-customer-name"),
        pytest.param("country", CRM_COLUMNS, id="country-gave-account-id"),
        pytest.param("amount", CRM_COLUMNS, id="amount-gave-account-id-memo"),
        # Close in letters, a different quantity: two edits, not a typo.
        pytest.param("quantity_lb", LAKE_COLUMNS, id="other-unit"),
        # One letter of a four-letter word is a different word.
        pytest.param("date", ["data"], id="short-word"),
    ],
)
def test_must_fail_nothing_close_gives_no_suggestion(unknown: str, columns: list[str]) -> None:
    """An absent column gets no hint. There is no fallback to the first columns."""
    assert plan_catalog.did_you_mean([unknown], columns) == ""
    detail = f"PLAN_SQL_GATE_REFUSED: UNKNOWN_COLUMN:{unknown}"
    assert plan_catalog.with_did_you_mean(detail, columns) == detail


@pytest.mark.parametrize(
    ("unknown", "columns", "hint"),
    [
        pytest.param("txn_typo", LAKE_COLUMNS, "did you mean: txn_type", id="replace"),
        pytest.param("txn_tpye", LAKE_COLUMNS, "did you mean: txn_type", id="swap"),
        pytest.param("skus", LAKE_COLUMNS, "did you mean: sku", id="insert"),
        pytest.param("emial", CRM_COLUMNS, "did you mean: email", id="crm-swap"),
        pytest.param("TXN_TYPO", LAKE_COLUMNS, "did you mean: txn_type", id="case"),
    ],
)
def test_did_you_mean_names_a_typo_of_the_unknown_column(
    unknown: str, columns: list[str], hint: str
) -> None:
    assert plan_catalog.did_you_mean([unknown], columns) == hint


def test_must_fail_a_suggestion_never_names_an_ungranted_column() -> None:
    """The decoy ``secret_col`` is a typo match for ``secret_cot``, and it sits
    in a table outside the grant. Neither the schema string nor the payload can
    put it in the pool the hint draws from."""
    from cortex_contract.answer import AskPayload

    decoy, unknown = UNGRANTED_COLUMN, "secret_cot"
    # The decoy is close: a pool that held it would offer it.
    assert plan_catalog.did_you_mean([unknown], ["txn_type", decoy]) == f"did you mean: {decoy}"

    # schema_context declares the ungranted table; narrow drops it.
    payload = AskPayload.model_validate(PAYLOAD)
    request = PlanSqlRequest("q", payload, schema_context=SCHEMA_CONTEXT)
    narrowed, code = plan_catalog.narrow(request, GRANT)
    assert code == ""
    pool = plan_catalog.column_names(narrowed, GRANT)
    assert pool == ("txn_type",)
    assert plan_catalog.did_you_mean([unknown], pool) == ""
    gate = f"PLAN_SQL_GATE_REFUSED: UNKNOWN_COLUMN:{unknown}"
    assert plan_catalog.with_did_you_mean(gate, pool) == gate
    db_error = f'BINDER_ERROR: Referenced column "{unknown}" not found in FROM clause!'
    assert plan_catalog.unknown_column_hint(db_error, f"SELECT {unknown} FROM t", pool) == ""

    # A request that still carries an ungranted table (a direct caller that
    # skipped the widening check): its columns are not in the pool either.
    widened = PlanSqlRequest(
        "q",
        AskPayload.model_validate(
            {
                "tables": [
                    {"name": "transactions", "columns": [{"name": "txn_type"}]},
                    {"name": UNGRANTED_TABLE, "columns": [{"name": decoy}]},
                ]
            }
        ),
    )
    pool = plan_catalog.column_names(widened, GRANT)
    assert decoy not in pool
    assert plan_catalog.with_did_you_mean(gate, pool) == gate


def test_executor_hint_names_only_the_unknown_column_and_a_typo() -> None:
    """DuckDB's two wordings, the qualified one included. The database's own
    candidate list is not echoed, and a name the model's SQL did not write
    (a manifest predicate column) is not named."""
    pool = ["txn_type", "sku"]
    sql = "SELECT t.txn_typo FROM transactions t"
    for text in (
        'BINDER_ERROR: Table "t" does not have a column named "txn_typo"',
        'BINDER_ERROR: Referenced column "txn_typo" not found in FROM clause! '
        'Candidate bindings: "transactions.passport_no"',
    ):
        assert plan_catalog.unknown_column_names(text) == ("txn_typo",)
        hint = plan_catalog.unknown_column_hint(text, sql, pool)
        assert hint == "UNKNOWN_COLUMN:txn_typo; did you mean: txn_type"
        assert "passport_no" not in hint
    predicate = 'BINDER_ERROR: Table "t" does not have a column named "sku_"'
    assert plan_catalog.unknown_column_hint(predicate, sql, pool) == ""
    nothing_close = 'BINDER_ERROR: Table "t" does not have a column named "revenue"'
    assert plan_catalog.unknown_column_hint(nothing_close, "SELECT revenue FROM t", pool) == ""


def test_must_fail_absent_measure_gets_no_hint_and_reconfirms(
    armed_openvault,
    dms_http,  # noqa: F811
    lake_spy: LakeSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review repro on f2a26a3: the retry read ``UNKNOWN_COLUMN:revenue; did you
    mean: txn_type, sku, quantity_kg``, and a model that took the offer was
    served kilogram sums labelled ``revenue``. Now nothing is offered by Cortex
    or by DuckDB's binder (whose candidate list named ``reference_doc``, a
    column outside the prompt), and with no stronger route the ladder ends in
    reconfirm with no rows."""
    _stronger_route(armed_openvault, "", monkeypatch)
    # Refused by the gate's column check, then (aliased) by DuckDB's binder.
    _reply(armed_openvault, "SELECT txn_type, revenue FROM transactions")
    _reply(
        armed_openvault,
        "SELECT txn_type, SUM(revenue) AS revenue FROM transactions GROUP BY txn_type",
    )
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, payload=UNMASKED_PAYLOAD, question="what is total revenue"))

    prompts = _prompts(armed_openvault)
    assert len(prompts) == 2
    assert "UNKNOWN_COLUMN:revenue" in prompts[1]
    for prompt in prompts:
        lowered = prompt.lower()
        assert "did you mean" not in lowered, prompt
        assert "candidate bindings" not in lowered, prompt
        assert "reference_doc" not in prompt, prompt
    # The gate refused the first SQL before the executor; the executor's
    # EXPLAIN refused the second before any row was read.
    assert not any("SELECT txn_type, revenue FROM" in sql for sql in lake_spy.sql)
    assert all("SUM(revenue)" in sql for sql in _ran(lake_spy.sql))

    closest = "What are the distinct txn_type values in transactions?"
    assert body["provenance"]["badge"] == "reconfirm", body
    assert _badge(body).lower() == "reconfirm", body
    assert body["route"] == "reconfirm"
    assert body["plan_sql_rung"] == plan_sql_ask.RUNG_RECONFIRM
    assert body["rows"] in ([], None) and body["row_count"] == 0
    assert body["sql_used"] is None
    assert body["drillthrough_token"] is None
    why = body["reconfirm"]["why"]
    assert why.startswith(f"{plan_sql_ask.MODEL_UNAVAILABLE}: "), why
    assert body["reconfirm"] == {"why": why, "closest_question": closest}
    assert body["answer"] == plan_sql_ask.reconfirm_answer(why, closest)
    served = json.dumps(body).lower()
    for text in ("did you mean", "candidate bindings", "reference_doc", "quantity_kg"):
        assert text not in served, text


def test_retry_detail_drops_the_databases_own_suggestions() -> None:
    """DuckDB lists candidates whether or not they are close or in the prompt.
    Only :func:`did_you_mean` may offer a name."""
    pool = ["txn_type", "sku"]
    nothing_close = (
        "PLAN_SQL_GATE_REFUSED: EXPLAIN_FAILED:BINDER_ERROR: Referenced column "
        '"revenue" not found in FROM clause! Candidate bindings: "reference_doc", "txn_type"'
    )
    assert plan_catalog.retry_detail(nothing_close, pool) == (
        "PLAN_SQL_GATE_REFUSED: EXPLAIN_FAILED:BINDER_ERROR: Referenced column "
        '"revenue" not found in FROM clause!'
    )
    typo = (
        'BINDER_ERROR: Referenced column "txn_typo" not found in FROM clause! '
        'Candidate bindings: "transactions.passport_no"'
    )
    assert plan_catalog.retry_detail(typo, pool) == (
        'BINDER_ERROR: Referenced column "txn_typo" not found in FROM clause!; '
        "did you mean: txn_type"
    )
    table = 'CATALOG_ERROR: Table with name ledger does not exist! Did you mean "other_ledger"?'
    assert plan_catalog.retry_detail(table, pool) == (
        "CATALOG_ERROR: Table with name ledger does not exist!"
    )


def test_must_fail_hint_never_names_an_ungranted_column_over_http(
    armed_openvault,
    dms_http,  # noqa: F811
) -> None:
    """The model writes a typo of the ungranted ``secret_col``. The retry names
    the unknown column and offers nothing: ``txn_type`` is not close and
    ``secret_col`` is outside the grant. The old fallback offered ``txn_type``."""
    _reply(armed_openvault, "SELECT secret_cot FROM transactions")
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, payload=PAYLOAD, schema_context=SCHEMA_CONTEXT))

    prompts = _prompts(armed_openvault)
    assert len(prompts) == 2
    assert "UNKNOWN_COLUMN:secret_cot" in prompts[1]
    for prompt in prompts:
        assert "did you mean" not in prompt, prompt
        for name in (UNGRANTED_COLUMN, UNGRANTED_TABLE, GHOST):
            assert name not in prompt, name
    _assert_served(body, GOOD_SQL, plan_sql_ask.rung_retry(1))
    for name in (UNGRANTED_COLUMN, UNGRANTED_TABLE, "secret_cot"):
        assert name not in json.dumps(body), name


# -- sampled values: only columns DMS marked unmasked ----------------------------

DECOY = "zz-decoy-passport-7Q4X"
SAMPLE_PREFIX = 'SELECT DISTINCT "'


class LakeSpy:
    """Every SQL Cortex runs, and a decoy the lake "holds" in chosen columns.

    A distinct-value probe on a column in ``decoys`` returns that value, as if
    the test lake stored it. Every other statement runs on the real lake.
    """

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.decoys: dict[str, str] = {}

    def probed(self) -> list[str]:
        return [sql.split('"', 2)[1] for sql in self.sql if sql.startswith(SAMPLE_PREFIX)]


@pytest.fixture
def lake_spy(monkeypatch: pytest.MonkeyPatch) -> LakeSpy:
    from CortexOS.execution import submit

    real = submit.execute_sql
    spy = LakeSpy()

    def run(verified, sql, **kwargs):  # noqa: ANN001
        spy.sql.append(sql)
        if sql.startswith(SAMPLE_PREFIX):
            column = sql.split('"', 2)[1]
            if column in spy.decoys:
                return ([{"v": spy.decoys[column]}], 0.0, 0.0)
        return real(verified, sql, **kwargs)

    monkeypatch.setattr(submit, "execute_sql", run)
    return spy


def _sku_payload(sku_mask: dict[str, Any]) -> dict[str, Any]:
    return {
        "tables": [
            {
                "name": "transactions",
                "columns": [
                    {"name": "txn_type", "type": "VARCHAR", "masked": False},
                    {"name": "sku", "type": "VARCHAR", **sku_mask},
                ],
            }
        ]
    }


def _sample_line(body: dict[str, Any]) -> str:
    return next(line for line in body["assumptions"] if line.startswith("step sample:"))


@pytest.mark.parametrize(
    "sku_mask",
    [
        pytest.param({"masked": True}, id="masked"),
        pytest.param({}, id="no-mask-state"),
        pytest.param({"masked": None}, id="null-mask-state"),
    ],
)
def test_must_fail_masked_column_values_never_reach_the_model(
    armed_openvault,
    dms_http,  # noqa: F811
    lake_spy: LakeSpy,
    sku_mask: dict[str, Any],
) -> None:
    """Security ruling on f2a26a3: the sampler read every declared column into
    the prompt. A column marked masked, or with no mask state, keeps its name
    and type in the prompt; it is never probed and its decoy value never
    reaches the OpenVault request body."""
    lake_spy.decoys["sku"] = DECOY
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, payload=_sku_payload(sku_mask)))

    assert lake_spy.probed() == ["txn_type"]
    (call,) = armed_openvault.chat_calls
    assert DECOY not in json.dumps(call["body"])
    (prompt,) = _prompts(armed_openvault)
    assert "- transactions(txn_type VARCHAR, sku VARCHAR)" in prompt.splitlines(), prompt
    assert "- transactions.txn_type: " in prompt
    assert "transactions.sku:" not in prompt
    assert "masked=1" in _sample_line(body)
    _assert_served(body, GOOD_SQL, plan_sql_ask.RUNG_PLAN)
    assert DECOY not in json.dumps(body)


def test_unmasked_column_decoy_reaches_the_prompt(
    armed_openvault,
    dms_http,  # noqa: F811
    lake_spy: LakeSpy,
) -> None:
    """Control for the test above: with ``masked: false`` the same decoy is in
    the prompt, so its absence there is the mask, not the harness."""
    lake_spy.decoys["sku"] = DECOY
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, payload=_sku_payload({"masked": False})))

    assert lake_spy.probed() == ["txn_type", "sku"]
    (prompt,) = _prompts(armed_openvault)
    assert f"- transactions.sku: {DECOY!r}" in prompt.splitlines(), prompt
    assert "masked=0" in _sample_line(body)
    _assert_served(body, GOOD_SQL, plan_sql_ask.RUNG_PLAN)


def test_must_fail_schema_context_column_the_payload_does_not_mark_is_not_sampled(
    armed_openvault,
    dms_http,  # noqa: F811
    lake_spy: LakeSpy,
) -> None:
    """The string declares ``sku``; the payload does not list it, so it has no
    mask state. Its name and type reach the prompt, its values do not."""
    schema = "\n".join(
        [
            "DIALECT: duckdb",
            "SCHEMA",
            "- transactions reason=score:1",
            "- txn_type VARCHAR reason=kept",
            "- sku VARCHAR description=DMSMASK_sku_01 reason=kept",
        ]
    )
    payload = {
        "tables": [
            {"name": "transactions", "columns": [{"name": "txn_type", "masked": False}]}
        ]
    }
    lake_spy.decoys["sku"] = DECOY
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, payload=payload, schema_context=schema))

    assert lake_spy.probed() == ["txn_type"]
    (call,) = armed_openvault.chat_calls
    assert DECOY not in json.dumps(call["body"])
    (prompt,) = _prompts(armed_openvault)
    assert "- transactions(txn_type VARCHAR, sku VARCHAR)" in prompt.splitlines(), prompt
    assert "transactions.sku:" not in prompt
    _assert_served(body, GOOD_SQL, plan_sql_ask.RUNG_PLAN)
    assert DECOY not in json.dumps(body)


def test_dms_render_samples_only_columns_marked_unmasked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The literal DMS CRM render with both tables granted. ``passport_no`` is
    marked masked, ``date_of_birth`` and the rest carry no mask state, and
    ``status`` is marked unmasked: only ``status`` is probed."""
    from cortex_contract.answer import AskPayload

    from CortexOS.execution import submit

    probed: list[str] = []

    def run(verified, sql, **kwargs):  # noqa: ANN001
        probed.append(sql)
        return ([{"v": DECOY}], 0.0, 0.0)

    monkeypatch.setattr(submit, "execute_sql", run)
    payload = AskPayload.model_validate(
        {
            "tables": [
                {
                    "name": "account",
                    "columns": [{"name": "account_id"}, {"name": "status", "masked": False}],
                },
                {
                    "name": "contact",
                    "columns": [
                        {"name": "passport_no", "masked": True},
                        {"name": "date_of_birth"},
                        {"name": "email", "masked": None},
                    ],
                },
            ]
        }
    )
    request = PlanSqlRequest("q", payload, schema_context=_load("dms_schema_context_crm.txt"))
    narrowed, code = plan_catalog.narrow(request, {"account", "contact"})
    assert code == ""
    states = {
        (t.name, c.name): c.masked for t in narrowed.payload.tables for c in t.columns
    }
    assert states[("account", "status")] is False
    assert {key for key, masked in states.items() if masked is False} == {("account", "status")}

    sampled, reason = plan_sql_ask._sample_values(object(), narrowed)

    assert [sql.split('"', 2)[1] for sql in probed] == ["status"]
    assert sampled == {"account": {"status": (DECOY,)}}
    assert reason == f"columns=1 values=1 errors=0 masked={len(states) - 1}"


def test_unmasked_columns_fail_closed() -> None:
    """A column listed twice is unmasked only when every listing says so."""
    from cortex_contract.answer import AskPayload

    payload = AskPayload.model_validate(
        {
            "tables": [
                {
                    "name": "Transactions",
                    "columns": [
                        {"name": "txn_type", "masked": False},
                        {"name": "sku", "masked": False},
                        {"name": "quantity_kg", "masked": False},
                        {"name": "unit"},
                    ],
                },
                {
                    "name": "transactions",
                    "columns": [{"name": "SKU", "masked": True}, {"name": "quantity_kg"}],
                },
            ]
        }
    )
    assert plan_catalog.unmasked_columns(payload) == frozenset({("transactions", "txn_type")})


# -- the type token: a SQL type word, never a name --------------------------------


def test_type_token_is_a_sql_type_word_not_a_name() -> None:
    """Security on f2a26a3: ``- qty passport_no`` and ``- txn_type other_ledger``
    put a column and a table name into the prompt as types."""
    parsed = schema_ctx.parse_schema_context(
        "\n".join(
            [
                "SCHEMA",
                "- transactions",
                f"- txn_type {UNGRANTED_TABLE} reason=x",
                "- qty passport_no",
                "- a varchar(20)",
                "- b decimal(12,2)",
                "- c BIGINT samples=1",
                "- d primary_key",
                "- e date description=x",
            ]
        )
    )
    assert parsed.tables == {"transactions": ["txn_type", "qty", "a", "b", "c", "d", "e"]}
    assert parsed.column_types == {
        "transactions": {"a": "varchar(20)", "b": "decimal(12,2)", "c": "BIGINT", "e": "date"}
    }


def test_must_fail_ungranted_table_name_never_reaches_the_prompt_as_a_type(
    armed_openvault,
    dms_http,  # noqa: F811
) -> None:
    """Review repro on f2a26a3: the prompt read ``- transactions(txn_type other_ledger)``."""
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)
    schema = f"SCHEMA\n- transactions\n- txn_type {UNGRANTED_TABLE} reason=x"

    body = _json(_ask(dms_http, payload=PAYLOAD, schema_context=schema))

    (prompt,) = _prompts(armed_openvault)
    assert "- transactions(txn_type)" in prompt.splitlines(), prompt
    assert UNGRANTED_TABLE not in prompt
    _assert_served(body, GOOD_SQL, plan_sql_ask.RUNG_PLAN)
    assert UNGRANTED_TABLE not in json.dumps(body)


# -- the field: contract 1.7.0, the insights rule --------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [
        # 4097 two-byte characters: under 8192 characters, over 8192 UTF-8 bytes.
        pytest.param("é" * 4097, "schema_context_too_large", id="over-byte-cap"),
        pytest.param(
            "SCHEMA\n- transactions\n- txn_type VARCHAR ‮",
            "schema_context_forbidden_char",
            id="bidi-override",
        ),
        pytest.param("count the types, please", "schema_context_unparsed", id="no-catalog"),
        pytest.param(
            "SCHEMA\n- other ledger reason=score:0.2 description=x\n- secret_col INTEGER",
            "schema_context_unparsed",
            id="head-name-with-a-space",
        ),
        pytest.param(
            "SCHEMA\n- transactions reason=score:1\nJOINS\n- a.b = c.d",
            "schema_context_unparsed",
            id="undeclared-join",
        ),
    ],
)
def test_ask_refuses_schema_context_by_the_schema_context_01_rule(
    armed_openvault,
    dms_http,  # noqa: F811
    value: str,
    code: str,
) -> None:
    """The ask route and POST /v1/insights share one rule and one status.

    A value that rule refuses with a 422 is a 422 with its code here, before
    any model call.
    """
    found = schema_ctx.problem(value)
    assert found is not None and found[0] == code
    assert schema_ctx.problem_status(code) == 422
    dms_http.bind_session(SESSION, GRANT)

    resp = _ask(dms_http, payload=PAYLOAD, schema_context=value)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == code
    assert armed_openvault.chat_calls == []


@pytest.mark.parametrize(
    ("value", "error_type"),
    [
        pytest.param(123, "string_type", id="not-a-string"),
        pytest.param("x" * 8193, "string_too_long", id="over-max-length"),
    ],
)
def test_ask_schema_context_is_typed_by_the_contract(
    armed_openvault,
    dms_http,  # noqa: F811
    value: object,
    error_type: str,
) -> None:
    """``AskRequest.schema_context`` is ``string | null`` with maxLength 8192."""
    dms_http.bind_session(SESSION, GRANT)

    resp = _ask(dms_http, payload=PAYLOAD, schema_context=value)

    assert resp.status_code == 422, resp.text
    errors = resp.json()["detail"]
    assert any(
        err["loc"] == ["body", "schema_context"] and err["type"] == error_type for err in errors
    ), errors
    assert armed_openvault.chat_calls == []


def test_schema_context_that_declares_no_table_is_a_named_abstain(
    armed_openvault,
    dms_http,  # noqa: F811
) -> None:
    """DMS's over-budget fallback: a 200 abstain by name, as on /v1/insights."""
    value = "DIALECT: mysql\nSCHEMA"
    assert schema_ctx.problem(value)[0] == schema_ctx.SCHEMA_CONTEXT_EMPTY
    assert schema_ctx.problem_status(schema_ctx.SCHEMA_CONTEXT_EMPTY) == 200
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, payload=PAYLOAD, schema_context=value))

    _assert_named_abstain(body, schema_ctx.SCHEMA_CONTEXT_EMPTY)
    assert body["route"] == "abstain"
    assert armed_openvault.chat_calls == []


def test_schema_context_with_no_granted_table_is_a_named_refusal(
    armed_openvault,
    dms_http,  # noqa: F811
) -> None:
    value = "\n".join(
        [
            "SCHEMA",
            f"- {UNGRANTED_TABLE} reason=score:0.2 description={UNGRANTED_WORDS}",
            f"- {UNGRANTED_COLUMN} INTEGER",
        ]
    )
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, payload=PAYLOAD, schema_context=value))

    _assert_named_abstain(body, plan_catalog.SCHEMA_CONTEXT_UNGRANTED)
    assert armed_openvault.chat_calls == []
    for text in (UNGRANTED_TABLE, UNGRANTED_COLUMN, UNGRANTED_WORDS):
        assert text not in json.dumps(body), text


@pytest.mark.parametrize("blank", ["", "  \n\t "])
def test_blank_schema_context_is_absent(
    armed_openvault,
    dms_http,  # noqa: F811
    blank: str,
) -> None:
    """Blank is absent: the payload is the prompt, as on 1.6."""
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, payload=PAYLOAD, schema_context=blank))

    (prompt,) = _prompts(armed_openvault)
    assert GHOST in prompt
    _assert_served(body, GOOD_SQL, plan_sql_ask.RUNG_PLAN)


def test_contract_1_7_declares_schema_context_on_ask_request() -> None:
    from cortex_contract.answer import AskRequest
    from cortex_contract.version import CONTRACT_VERSION

    assert CONTRACT_VERSION == "1.7.0"
    assert "schema_context" in AskRequest.model_fields
    root = Path(__file__).resolve().parents[2] / "contract"
    spec = json.loads((root / "openapi-1.7.0.json").read_text(encoding="utf-8"))
    assert spec["info"]["version"] == "1.7.0"
    field = spec["components"]["schemas"]["AskRequest"]["properties"]["schema_context"]
    assert {"type": "string", "maxLength": 8192} in field["anyOf"], field
    assert {"type": "null"} in field["anyOf"], field
    assert "schema_context" not in spec["components"]["schemas"]["AskRequest"].get("required", [])
    # 1.6.0 is frozen: its AskRequest never had the field.
    old = json.loads((root / "openapi-1.6.0.json").read_text(encoding="utf-8"))
    assert "schema_context" not in old["components"]["schemas"]["AskRequest"]["properties"]


def test_contract_1_7_declares_the_column_mask_flag() -> None:
    """``AskPayloadColumn.masked``: optional, boolean or null, default null."""
    from cortex_contract.answer import AskPayloadColumn

    assert AskPayloadColumn(name="c").masked is None
    assert AskPayloadColumn(name="c", masked=False).masked is False
    root = Path(__file__).resolve().parents[2] / "contract"
    spec = json.loads((root / "openapi-1.7.0.json").read_text(encoding="utf-8"))
    old = json.loads((root / "openapi-1.6.0.json").read_text(encoding="utf-8"))
    for name in ("AskPayloadColumn", "ContractAskPayloadColumn"):
        schema = spec["components"]["schemas"][name]
        field = schema["properties"]["masked"]
        assert field["anyOf"] == [{"type": "boolean"}, {"type": "null"}], field
        assert "masked" not in schema.get("required", [])
        assert "masked" not in old["components"]["schemas"][name]["properties"]
