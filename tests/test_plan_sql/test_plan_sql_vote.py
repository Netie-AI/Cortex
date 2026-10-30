"""PLAN-SQL-VOTE-01: model SQL is served only when a second, independent SQL agrees.

Measured on BIRD Mini-Dev split A, from existing runs re-executed: when two
independent SQL candidates returned the same rows they were right 79-87% of the
time, and a candidate no second one matched was right 15-26% of the time. A
first SQL that ran is therefore a candidate. Assertions are on the envelope DMS
receives (CLAUDE.md section 8): the rendered answer, the rows, and what ran.
The model is the scripted OpenVault.

GEN-GRANTED-CATALOG-01 (#386) folds the plan into the one SQL call, so neither
reading carries a plan. The second reading has its own framing (system prompt
and user layout over the same granted catalog and question), and it runs only
because its prompt differs from the first's: Lead option (a) on #386.
"""

# ruff: noqa: F811  (pytest fixtures are imported, then requested by name)
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

import pytest

from CortexOS.dms import plan_sql_ask, plan_sql_vote
from CortexOS.plan_sql import payload as plan_payload
from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401
from tests.test_plan_sql.test_ai_sql_reads_table import _oracle, _rowset
from tests.test_plan_sql.test_c_loop_a_plan_sql import (  # noqa: F401
    GOOD_SQL,
    GRANT,
    PAYLOAD,
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
BAD_SQL = "SELECT no_such_col FROM transactions"
STRONG = "openai/gpt-oss-20b"
# A phrase only the second reading's system prompt carries.
SECOND_FRAMING = "independently"


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


def _sha(call: dict[str, Any]) -> str:
    """sha256 of the exact messages FreeRoute sent, computed here, not by the engine."""
    canon = json.dumps(
        call["body"]["messages"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _system(call: dict[str, Any]) -> str:
    return call["body"]["messages"][0]["content"]


def _user(call: dict[str, Any]) -> str:
    return call["body"]["messages"][1]["content"]


def _same_prompt_for_both_readings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the second reading's framing the first's: the two prompts are identical."""

    def _first_framing(request, *, prior_violations=()):  # noqa: ANN001
        return plan_payload.sql_messages(request, (), prior_violations=prior_violations)

    monkeypatch.setattr(plan_payload, "second_reading_messages", _first_framing)


# -- #421 PLAN-SQL-VOTE-01, retargeted to the folded ladder ----------------------


def test_a_second_sql_with_the_same_rows_serves_the_first(
    armed_openvault, dms_http, execute_spy: list[str]
) -> None:
    _distinct_readings()
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, SAME_ROWS_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    # Folded: the served first reading had no plan, so no plan line is attached.
    assert not any(line.startswith("plan: ") for line in body["assumptions"])
    ran = _ran(execute_spy)
    assert len(ran) == 2 and ran[0] == body["sql_used"] and ran[1].startswith(SAME_ROWS_SQL)
    first, second = armed_openvault.chat_calls
    # The second reading is its own framing over the same catalog and question.
    assert SECOND_FRAMING in _system(second)
    assert SECOND_FRAMING not in _system(first)
    assert "PLAN:" not in _user(second)
    assert "PLAN:" not in _user(first)
    assert _sha(first) != _sha(second)
    assert body["model_calls"] == {"plan": 2, "total": 2}
    assert any("agreed" in line for line in _vote_lines(body)), body["assumptions"]
    _assert_rung(body, plan_sql_ask.RUNG_PLAN)


def test_readings_that_disagree_with_no_stronger_route_serve_nothing(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _distinct_readings()
    _stronger_route(armed_openvault, "", monkeypatch)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, SKU_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_reconfirmed(body)
    assert len(armed_openvault.chat_calls) == 2
    # The split goes straight to reconfirm: no further write is voted again.
    assert not any(line.startswith("step escalate:") for line in body["assumptions"])
    assert len(_ran(execute_spy)) == 2


def test_the_stronger_route_sides_with_the_second_reading(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _distinct_readings()
    _stronger_route(armed_openvault, STRONG, monkeypatch)
    _reply(armed_openvault, SKU_SQL)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, SAME_ROWS_SQL, model=STRONG)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    models = [call["body"]["model"] for call in armed_openvault.chat_calls]
    assert models[2] == STRONG and models[0] == models[1] != STRONG
    # The served SQL had no plan, so no plan line is attached to it.
    assert not any(line.startswith("plan: ") for line in body["assumptions"])
    assert body["model_calls"] == {"plan": 2, "stronger-model": 1, "total": 3}
    assert any("second SQL's rows" in line for line in _vote_lines(body)), body["assumptions"]


def test_the_stronger_route_sides_with_the_first_reading(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    _distinct_readings()
    _stronger_route(armed_openvault, STRONG, monkeypatch)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, KG_SQL)
    _reply(armed_openvault, SAME_ROWS_SQL, model=STRONG)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    # The served first reading's plan lines are on the answer: folded, there are none.
    assert not any(line.startswith("plan: ") for line in body["assumptions"])
    assert any("first SQL's rows" in line for line in _vote_lines(body)), body["assumptions"]


def test_three_readings_that_disagree_reconfirm(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    _distinct_readings()
    _stronger_route(armed_openvault, STRONG, monkeypatch)
    closest = "how many transactions are there of each txn_type"
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, SKU_SQL)
    _reply(armed_openvault, KG_SQL, model=STRONG)
    _reply(armed_openvault, f"WHY: count rows or SKUs is unclear\nCLOSEST: {closest}", model=STRONG)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_reconfirmed(body)
    assert closest in body["answer"]
    assert len(armed_openvault.chat_calls) == 4
    assert plan_sql_vote.VOTE_SPLIT in _user(armed_openvault.chat_calls[3])


# -- #422 PLAN-SQL-VOTE-02, retargeted to the folded ladder ----------------------


def test_the_same_sql_written_twice_counts_as_agreement(
    armed_openvault, dms_http, execute_spy: list[str]
) -> None:
    """Measured on 500 BIRD questions: the same SQL with and without the plan was
    right 41 times in 49, better than agreement between different SQL. Folded,
    the two prompts differ in framing instead of in a plan."""
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, f"  {GOOD_SQL.lower()} ;")
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    assert any("the same SQL" in line for line in _vote_lines(body)), body["assumptions"]
    first, second = armed_openvault.chat_calls
    assert _sha(first) != _sha(second)
    _assert_rung(body, plan_sql_ask.RUNG_PLAN)


def test_a_second_reading_that_fails_is_fed_back_and_retried(
    armed_openvault, dms_http, execute_spy: list[str]
) -> None:
    """Measured: in 21 of 221 right first answers the second reading was refused,
    failed EXPLAIN or returned no rows. It gets the ladder's error-fed retry."""
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, BAD_SQL)
    _reply(armed_openvault, SAME_ROWS_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    calls = armed_openvault.chat_calls
    retry = _user(calls[2])
    assert "UNKNOWN_COLUMN:no_such_col" in retry
    assert "PLAN:" not in retry
    # The retry stays in the second reading's framing and never repeats a first prompt.
    assert SECOND_FRAMING in _system(calls[2])
    assert _sha(calls[0]) not in {_sha(calls[1]), _sha(calls[2])}
    assert body["model_calls"] == {"plan": 2, "error-fed-retry-1": 1, "total": 3}
    assert any("a second SQL" in line for line in _vote_lines(body)), body["assumptions"]


def test_a_second_reading_whose_prompt_would_repeat_the_first_is_skipped(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#422's guard under the fold: the folded first SQL never has a plan, so the
    guard is the prompt hash. Two readings of one prompt would always agree; the
    vote must not mistake that for confirmation. With no stronger route, nothing
    can agree and the ladder reconfirms."""
    _same_prompt_for_both_readings(monkeypatch)
    _stronger_route(armed_openvault, "", monkeypatch)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_reconfirmed(body)
    # The one SQL write: no second reading from the same prompt is sent.
    assert len(armed_openvault.chat_calls) == 1
    assert "PLAN:" not in _user(armed_openvault.chat_calls[0])
    assert any(
        "no second reading" in line and "first reading's prompt" in line
        for line in _vote_lines(body)
    ), body["assumptions"]
    assert not any(line.startswith("step vote-sql:") for line in body["assumptions"])
    assert len(_ran(execute_spy)) == 1


def test_a_skipped_second_reading_leaves_the_vote_to_the_stronger_route(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipped for an identical prompt, the vote relies on the stronger route: its
    reading with the first's rows serves the first."""
    _distinct_readings()
    _same_prompt_for_both_readings(monkeypatch)
    _stronger_route(armed_openvault, STRONG, monkeypatch)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, SAME_ROWS_SQL, model=STRONG)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    models = [call["body"]["model"] for call in armed_openvault.chat_calls]
    assert len(models) == 2 and models[1] == STRONG != models[0]
    lines = _vote_lines(body)
    assert any("no second reading" in line for line in lines), body["assumptions"]
    assert any("first SQL's rows" in line for line in lines), body["assumptions"]
    assert body["model_calls"] == {"plan": 1, "stronger-model": 1, "total": 2}
    _assert_rung(body, plan_sql_ask.RUNG_PLAN)


def test_a_generator_that_sends_the_first_prompt_again_gets_no_vote(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generator that ignores ``reading`` and sends the first prompt again: its
    rows match, but a reading of the same prompt is not counted as agreement."""
    from CortexOS.plan_sql import generator

    class _IgnoresReading(generator.FreeRoutePlanSqlGenerator):
        def sql(self, request, plan, *, prior_violations=(), reading=""):  # noqa: ANN001
            del reading
            return super().sql(request, plan, prior_violations=prior_violations)

    monkeypatch.setattr(plan_sql_ask, "default_generator", _IgnoresReading)
    _stronger_route(armed_openvault, "", monkeypatch)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_reconfirmed(body)
    first, second = armed_openvault.chat_calls
    assert _sha(first) == _sha(second)
    assert any("not shown to differ" in line for line in _vote_lines(body)), body["assumptions"]
    assert not any("agreed" in line for line in _vote_lines(body)), body["assumptions"]
    # The repeated SQL is never executed as a vote.
    assert len(_ran(execute_spy)) == 1


@pytest.mark.parametrize(
    "replies",
    [
        pytest.param([GOOD_SQL, SAME_ROWS_SQL], id="first-writes"),
        pytest.param([BAD_SQL, GOOD_SQL, BAD_SQL, SAME_ROWS_SQL], id="error-fed-retries"),
    ],
)
def test_must_fail_the_two_readings_never_share_a_prompt_hash(
    armed_openvault, dms_http, execute_spy: list[str], replies: list[str]
) -> None:
    """Lead option (a), #386: with CORTEX_PLAN_SQL_VOTE=1 the second reading never
    sends a prompt the first reading sent (sha256 of the exact messages). Fails if
    the second reading reuses the first prompt: then either it is skipped (one
    reading only, nothing to compare) or it is sent and the hashes match."""
    for content in replies:
        _reply(armed_openvault, content)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    calls = armed_openvault.chat_calls
    assert len(calls) == len(replies), "the second reading must run"
    half = len(calls) // 2
    first_reading = {_sha(call) for call in calls[:half]}
    second_reading = {_sha(call) for call in calls[half:]}
    assert first_reading.isdisjoint(second_reading)
    assert all(SECOND_FRAMING in _system(call) for call in calls[half:])
    # The engine's own hash names the same bytes FreeRoute was sent.
    served_first = _sha(calls[half - 1])
    lines = _vote_lines(body)
    for sent in second_reading:
        assert any(
            f"sha256={sent[:12]}" in line and f"sha256={served_first[:12]}" in line
            for line in lines
        ), body["assumptions"]
    assert any("agreed" in line for line in lines), body["assumptions"]


def test_the_second_reading_prompt_keeps_the_catalog_and_the_sql_rules() -> None:
    """The second framing is built from the same granted catalog, sampled values and
    question as the first, with #420's rules, and is a different prompt."""
    request = plan_payload.PlanSqlRequest(
        "kg of SKU-ZZZZZ",
        plan_payload.AskPayload.model_validate(PAYLOAD),
        column_values={"transactions": {"sku": ("SKU-00001",)}},
    )
    first = plan_payload.reading_messages(request, ())
    second = plan_payload.reading_messages(request, (), reading=plan_payload.SECOND_READING)
    assert first == plan_payload.sql_messages(request, ())
    assert second == plan_payload.second_reading_messages(request)
    assert plan_payload.prompt_sha256(first) != plan_payload.prompt_sha256(second)
    assert first[0]["content"] != second[0]["content"]
    text = second[1]["content"]
    assert text.startswith("QUESTION: kg of SKU-ZZZZZ")
    assert plan_payload.schema_block(request) in text
    assert plan_payload.values_block(request) in text
    assert "PLAN:" not in text
    for rule in (
        "Use ONLY the tables, columns and joins listed",
        "Never replace a value the question names",
        "Return the columns the question asks for",
        "Return SQL only",
    ):
        assert rule in second[0]["content"]
    retry = plan_payload.second_reading_messages(request, prior_violations=["GATE: x"])
    assert "PREVIOUS VALIDATION ERRORS (fix these):\n- GATE: x" in retry[1]["content"]
    assert plan_payload.prompt_sha256(first) == hashlib.sha256(
        json.dumps(first, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


# -- unchanged by the fold -------------------------------------------------------


def test_a_second_reading_without_a_route_stamp_stops_the_answer(
    armed_openvault, dms_http, execute_spy: list[str]
) -> None:
    """A vote step is a model step: a response OpenVault did not stamp is refused."""
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
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_served(body, GOOD_SQL)
    assert len(armed_openvault.chat_calls) == 1
    assert _vote_lines(body) == []


def test_result_key_ignores_column_names_and_number_types() -> None:
    first = [{"kind": "IN", "n": 3}, {"kind": "OUT", "n": Decimal("2.00001")}]
    second = [{"txn_type": "OUT", "cnt": 2.0}, {"txn_type": "IN", "cnt": 3.0}]
    assert plan_sql_vote.agree(first, second)
    assert not plan_sql_vote.agree(first, [{"t": "IN", "n": 3}])
    assert not plan_sql_vote.agree([], [])
    assert plan_sql_vote.same_sql("SELECT 1;", " select   1 ")
    assert not plan_sql_vote.same_sql("SELECT 1", "SELECT 2")
