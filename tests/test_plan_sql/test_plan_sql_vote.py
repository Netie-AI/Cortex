"""PLAN-SQL-VOTE-01: model SQL is served only when a second, independent SQL agrees.

Measured on BIRD Mini-Dev split A, from existing runs re-executed: when two
independent SQL candidates returned the same rows they were right 79-87% of the
time, and a candidate no second one matched was right 15-26% of the time. A
first SQL that ran is therefore a candidate. Assertions are on the envelope DMS
receives (CLAUDE.md section 8): the rendered answer, the rows, and what ran.
The model is the scripted OpenVault.
"""

# ruff: noqa: F811  (pytest fixtures are imported, then requested by name)
from __future__ import annotations

from decimal import Decimal

import pytest

from CortexOS.dms import plan_sql_ask, plan_sql_vote
from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401
from tests.test_plan_sql.test_ai_sql_reads_table import _oracle, _rowset
from tests.test_plan_sql.test_c_loop_a_plan_sql import (  # noqa: F401
    GOOD_SQL,
    GRANT,
    PLAN_TEXT,
    SESSION,
    _ask,
    _assert_not_answered,
    _assert_rung,
    _json,
    _plan_sql_on,
    _ran,
    _reply,
    _stronger_route,
    execute_spy,
)

# Same rows as GOOD_SQL, written another way.
SAME_ROWS_SQL = "SELECT txn_type AS kind, COUNT(1) AS cnt FROM transactions GROUP BY 1"
# Different rows: other readings of "how many transactions of each type".
SKU_SQL = "SELECT txn_type, COUNT(DISTINCT sku) AS n FROM transactions GROUP BY txn_type"
KG_SQL = "SELECT txn_type, SUM(quantity_kg) AS n FROM transactions GROUP BY txn_type"
STRONG = "openai/gpt-oss-20b"


@pytest.fixture(autouse=True)
def _vote_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(plan_sql_vote.ENABLED_ENV, "1")


def _distinct_readings() -> None:
    keys = {plan_sql_vote.result_key(_oracle(sql)) for sql in (GOOD_SQL, SKU_SQL, KG_SQL)}
    assert len(keys) == 3, "the test lake must give the three readings different rows"
    assert plan_sql_vote.result_key(_oracle(SAME_ROWS_SQL)) == plan_sql_vote.result_key(
        _oracle(GOOD_SQL)
    )


def _assert_served(body: dict, sql: str) -> None:
    oracle = _oracle(sql)
    assert _rowset(body["rows"]) == _rowset(oracle)
    assert body["sql_used"].startswith(sql)
    top = body["rows"][0]
    assert str(next(iter(top.values()))) in body["answer"], body["answer"]
    assert not body["answer"].startswith(("Reconfirm:", "Not answered", "No rows")), body["answer"]


def _assert_reconfirmed(body: dict) -> None:
    assert body["rows"] in ([], None)
    assert body["sql_used"] is None
    assert body["answer"].startswith("Reconfirm:"), body["answer"]
    assert plan_sql_vote.VOTE_SPLIT in " ".join(body["assumptions"]), body["assumptions"]
    _assert_rung(body, plan_sql_ask.RUNG_RECONFIRM)


def _vote_lines(body: dict) -> list[str]:
    return [line for line in body["assumptions"] if line.startswith("step vote")]


def test_a_second_sql_with_the_same_rows_serves_the_first(
    armed_openvault, dms_http, execute_spy: list[str]
) -> None:
    _distinct_readings()
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, SAME_ROWS_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    assert any(line.startswith("plan: ") for line in body["assumptions"])
    ran = _ran(execute_spy)
    assert len(ran) == 2 and ran[0] == body["sql_used"] and ran[1].startswith(SAME_ROWS_SQL)
    # The second reading is written from the question alone.
    second = armed_openvault.chat_calls[2]["body"]["messages"]
    assert "answers the question" in second[0]["content"]
    assert "PLAN:" not in second[1]["content"]
    assert "PLAN:" in armed_openvault.chat_calls[1]["body"]["messages"][1]["content"]
    assert body["model_calls"] == {"plan": 3, "total": 3}
    assert any("agreed" in line for line in _vote_lines(body)), body["assumptions"]
    _assert_rung(body, plan_sql_ask.RUNG_PLAN)


def test_readings_that_disagree_with_no_stronger_route_serve_nothing(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _distinct_readings()
    _stronger_route(armed_openvault, "", monkeypatch)
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, SKU_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_reconfirmed(body)
    assert len(armed_openvault.chat_calls) == 3
    # The split goes straight to reconfirm: no further write is voted again.
    assert not any(line.startswith("step escalate:") for line in body["assumptions"])
    assert len(_ran(execute_spy)) == 2


def test_the_stronger_route_sides_with_the_second_reading(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _distinct_readings()
    _stronger_route(armed_openvault, STRONG, monkeypatch)
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, SKU_SQL)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, SAME_ROWS_SQL, model=STRONG)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    models = [call["body"]["model"] for call in armed_openvault.chat_calls]
    assert models[3] == STRONG and models[0] == models[1] == models[2] != STRONG
    # The served SQL had no plan, so the first reading's plan is not attached to it.
    assert not any(line.startswith("plan: ") for line in body["assumptions"])
    assert body["model_calls"] == {"plan": 3, "stronger-model": 1, "total": 4}
    assert any("second SQL's rows" in line for line in _vote_lines(body)), body["assumptions"]


def test_the_stronger_route_sides_with_the_first_reading(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    _distinct_readings()
    _stronger_route(armed_openvault, STRONG, monkeypatch)
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, KG_SQL)
    _reply(armed_openvault, SAME_ROWS_SQL, model=STRONG)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    assert any(line.startswith("plan: ") for line in body["assumptions"])
    assert any("first SQL's rows" in line for line in _vote_lines(body)), body["assumptions"]


def test_three_readings_that_disagree_reconfirm(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    _distinct_readings()
    _stronger_route(armed_openvault, STRONG, monkeypatch)
    closest = "how many transactions are there of each txn_type"
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, SKU_SQL)
    _reply(armed_openvault, KG_SQL, model=STRONG)
    _reply(armed_openvault, f"WHY: count rows or SKUs is unclear\nCLOSEST: {closest}", model=STRONG)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_reconfirmed(body)
    assert closest in body["answer"]
    assert len(armed_openvault.chat_calls) == 5
    assert (
        plan_sql_vote.VOTE_SPLIT in armed_openvault.chat_calls[4]["body"]["messages"][1]["content"]
    )


def test_the_same_sql_written_twice_counts_as_agreement(
    armed_openvault, dms_http, execute_spy: list[str]
) -> None:
    """Measured on 500 BIRD questions: the same SQL with and without the plan was
    right 41 times in 49, better than agreement between different SQL."""
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, f"  {GOOD_SQL.lower()} ;")
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    assert any("the same SQL" in line for line in _vote_lines(body)), body["assumptions"]
    _assert_rung(body, plan_sql_ask.RUNG_PLAN)


def test_a_second_reading_that_fails_is_fed_back_and_retried(
    armed_openvault, dms_http, execute_spy: list[str]
) -> None:
    """Measured: in 21 of 221 right first answers the second reading was refused,
    failed EXPLAIN or returned no rows. It gets the ladder's error-fed retry."""
    bad = "SELECT no_such_col FROM transactions"
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, SAME_ROWS_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    retry = armed_openvault.chat_calls[3]["body"]["messages"][1]["content"]
    assert "UNKNOWN_COLUMN:no_such_col" in retry
    assert "PLAN:" not in retry
    assert body["model_calls"] == {"plan": 3, "error-fed-retry-1": 1, "total": 4}
    assert any("a second SQL" in line for line in _vote_lines(body)), body["assumptions"]


def test_a_first_sql_written_without_a_plan_has_no_same_prompt_second_reading(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the plan folded into the SQL call (#386) both readings would share one prompt
    and always agree. The vote must not mistake that for confirmation."""
    import dataclasses

    from CortexOS.plan_sql import generator

    real_plan = generator.FreeRoutePlanSqlGenerator.plan

    def _unplanned(self, request):  # noqa: ANN001
        return dataclasses.replace(real_plan(self, request), plan=())

    monkeypatch.setattr(generator.FreeRoutePlanSqlGenerator, "plan", _unplanned)
    _stronger_route(armed_openvault, "", monkeypatch)
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_reconfirmed(body)
    # The plan call and the one SQL write: no second reading from the same prompt.
    assert len(armed_openvault.chat_calls) == 2
    assert "PLAN:" not in armed_openvault.chat_calls[1]["body"]["messages"][1]["content"]
    assert any("without a plan" in line for line in _vote_lines(body)), body["assumptions"]


def test_a_second_reading_without_a_route_stamp_stops_the_answer(
    armed_openvault, dms_http, execute_spy: list[str]
) -> None:
    """A vote step is a model step: a response OpenVault did not stamp is refused."""
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, SAME_ROWS_SQL, provider="")
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_not_answered(body, plan_sql_ask.ROUTE_STAMP_MISSING)
    # The first SQL ran; the unstamped one never did.
    ran = _ran(execute_spy)
    assert len(ran) == 1 and ran[0].startswith(GOOD_SQL)


def test_vote_off_serves_the_first_sql_unchecked(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default off: the ladder is unchanged and makes no vote call."""
    monkeypatch.delenv(plan_sql_vote.ENABLED_ENV)
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    assert len(armed_openvault.chat_calls) == 2
    assert _vote_lines(body) == []


def test_result_key_ignores_column_names_and_number_types() -> None:
    first = [{"kind": "IN", "n": 3}, {"kind": "OUT", "n": Decimal("2.00001")}]
    second = [{"txn_type": "OUT", "cnt": 2.0}, {"txn_type": "IN", "cnt": 3.0}]
    assert plan_sql_vote.agree(first, second)
    assert not plan_sql_vote.agree(first, [{"t": "IN", "n": 3}])
    assert not plan_sql_vote.agree([], [])
    assert plan_sql_vote.same_sql("SELECT 1;", " select   1 ")
    assert not plan_sql_vote.same_sql("SELECT 1", "SELECT 2")
