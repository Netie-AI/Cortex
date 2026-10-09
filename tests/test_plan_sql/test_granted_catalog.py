"""GEN-GRANTED-CATALOG-01 must-fails.

Red on the C-LOOP-A head: the prompt is the full payload, an unknown-column
retry has no catalog hint, and the first attempt is a plan call plus a SQL
call. Green here: the prompt is the grant intersected with schema_context,
the retry carries a did-you-mean hint, and each attempt is one FreeRoute call.

Names in this file are synthetic. The warehouse table the fixture already
grants is only the session the HTTP seam executes under.
"""

from __future__ import annotations

from typing import Any

import pytest

from CortexOS.dms import plan_sql_ask
from CortexOS.memory import space_memory as sm
from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401
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


def test_must_fail_column_outside_granted_catalog_never_in_prompt_or_sql(
    armed_openvault, dms_http  # noqa: F811
) -> None:
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, payload=PAYLOAD, schema_context=SCHEMA_CONTEXT))

    blob = "\n".join(_prompts(armed_openvault))
    assert GHOST not in blob
    assert UNGRANTED_TABLE not in blob
    assert UNGRANTED_COLUMN not in blob
    assert "txn_type" in blob
    assert body["rows"] and body["sql_used"].startswith(GOOD_SQL)
    assert GHOST not in (body["sql_used"] or "")
    assert GHOST not in body["answer"]
    assert len(armed_openvault.chat_calls) == 1


def test_must_fail_unknown_column_retry_carries_did_you_mean(
    armed_openvault, dms_http  # noqa: F811
) -> None:
    # The non-SELECT reply is the base ladder's plan call. On this head it is
    # one unusable attempt, then the unknown-column attempt, then the rewrite.
    _reply(armed_openvault, "1. Ground the question in the listed columns.")
    _reply(armed_openvault, "SELECT txn_typo FROM transactions")
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["rows"] and body["sql_used"].startswith(GOOD_SQL)
    retries = [prompt for prompt in _prompts(armed_openvault) if "UNKNOWN_COLUMN" in prompt]
    assert retries
    retry = retries[0]
    assert "did you mean:" in retry
    assert "txn_type" in retry.split("did you mean:", 1)[1]
    assert body["served_provider"] == PROVIDER
    assert body["served_model"]


def test_must_fail_one_model_call_per_attempt(
    armed_openvault, dms_http  # noqa: F811
) -> None:
    """Fake OpenVault client: one chat call per generation attempt."""
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    first = _json(_ask(dms_http, question=QUESTION))

    assert len(armed_openvault.chat_calls) == 1
    assert [row[0] for row in _route_rows()] == ["plan-sql-sql"]
    assert first["rows"]

    start = len(armed_openvault.chat_calls)
    routes_before = len(_route_rows())
    _reply(armed_openvault, "SELECT txn_typo FROM transactions")
    _reply(armed_openvault, GOOD_SQL)
    second = _json(_ask(dms_http, question="count the types again"))
    added = len(armed_openvault.chat_calls) - start

    assert second["rows"] and second["plan_sql_rung"] == plan_sql_ask.rung_retry(1)
    assert added == 2
    tasks = [row[0] for row in _route_rows()]
    assert tasks[routes_before:] == ["plan-sql-sql", "plan-sql-sql"]
    assert "plan-sql-plan" not in tasks
