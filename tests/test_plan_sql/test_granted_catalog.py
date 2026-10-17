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
    _reply,
    _route_rows,
)

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
    dms_http,
    value: str,
    code: str,  # noqa: F811
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
    dms_http,
    value: object,
    error_type: str,  # noqa: F811
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
    dms_http,
    blank: str,  # noqa: F811
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
