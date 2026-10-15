"""DRILLTHROUGH-NO-500-01: drill-through of a plan+SQL (ladder) answer over HTTP.

POST /v1/contract/drillthrough with the token a plan+SQL answer carried must
return the contributing rows or a named 4xx. A 500 is never an answer.
Assertions are on the HTTP status and body DMS receives.
"""

from __future__ import annotations

from typing import Any

import pytest

from CortexOS.dms import plan_sql_ask
from CortexOS.memory import space_memory as sm
from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401
from tests.test_plan_sql.test_c_loop_a_plan_sql import (
    GRANT,
    PLAN_TEXT,
    SESSION,
    _ask,
    _json,
    _reply,
)

# Under the 5000-row drill limit on the test lake, so every counted row comes back.
GROUP_COUNT_SQL = (
    "SELECT txn_type, COUNT(*) AS n FROM transactions WHERE txn_type <> 'outbound' "
    "GROUP BY txn_type ORDER BY n DESC"
)


@pytest.fixture(autouse=True)
def _plan_sql_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(plan_sql_ask.ENABLED_ENV, "1")
    monkeypatch.delenv(sm.ENABLED_ENV, raising=False)
    monkeypatch.delenv(sm.SCORED_ROUND_ENV, raising=False)
    sm.reset_space_memory_for_tests()
    yield
    sm.reset_space_memory_for_tests()


@pytest.fixture
def drill_client(dms_http):  # noqa: F811
    """Same app, but a server exception comes back as a 500 response, not a raise."""
    from fastapi.testclient import TestClient

    return TestClient(
        dms_http.app,
        headers={"X-API-Key": "dms-demo-admin-key"},
        raise_server_exceptions=False,
    )


def _ladder_answer(fake, client, sql: str) -> dict[str, Any]:
    _reply(fake, PLAN_TEXT)
    _reply(fake, f"```sql\n{sql};\n```")
    client.bind_session(SESSION, GRANT)
    body = _json(_ask(client))
    assert body["provenance"]["layer"] == "plan_sql"
    assert body["drillthrough_token"]
    return body


def _oracle(sql: str) -> list[dict[str, Any]]:
    """Rows straight from the test lake, outside the drill path."""
    from CortexOS.dms.warehouse_db import DEFAULT_DB
    from CortexOS.execution.warehouse import get_connection

    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
    finally:
        con.close()


def test_group_by_count_answer_drills_to_contributing_rows(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
) -> None:
    answer = _ladder_answer(armed_openvault, dms_http, GROUP_COUNT_SQL)
    counts = {r["txn_type"]: int(r["n"]) for r in answer["rows"]}
    for txn_type, n in counts.items():
        assert f"txn_type={txn_type}, n={n}" in answer["answer"]

    resp = drill_client.post(
        "/v1/contract/drillthrough", json={"token": answer["drillthrough_token"]}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer_id"] == answer["answer_id"]
    assert body["session_id"] == SESSION
    rows = body["rows"]
    assert rows and body["row_count"] == len(rows)
    # The drilled rows are the rows the answer counted, group by group.
    drilled: dict[str, int] = {}
    for row in rows:
        drilled[row["txn_type"]] = drilled.get(row["txn_type"], 0) + 1
    assert drilled == counts
    assert body["total_count"] == sum(counts.values())
    assert body["approximate"] is False
    assert "*" not in body["sql_used"].split("FROM", 1)[0]
    oracle = _oracle(
        "SELECT txn_type, COUNT(*) AS n FROM transactions WHERE txn_type <> 'outbound' GROUP BY txn_type"
    )
    assert {r["txn_type"]: int(r["n"]) for r in oracle} == counts


def test_sum_answer_drills_to_contributing_rows(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
) -> None:
    """Positive control: a plain aggregate answer drills to its rows."""
    sql = "SELECT SUM(quantity_kg) AS total FROM transactions WHERE txn_type = 'inbound'"
    answer = _ladder_answer(armed_openvault, dms_http, sql)

    resp = drill_client.post(
        "/v1/contract/drillthrough", json={"token": answer["drillthrough_token"]}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    oracle = _oracle("SELECT quantity_kg FROM transactions WHERE txn_type = 'inbound'")
    assert body["total_count"] == len(oracle)
    assert sorted(float(r["total"]) for r in body["rows"]) == sorted(
        float(r["quantity_kg"]) for r in oracle
    )
    assert (
        abs(sum(float(r["total"]) for r in body["rows"]) - float(answer["rows"][0]["total"])) < 1e-6
    )


def test_drill_sql_the_database_rejects_is_a_named_4xx(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
) -> None:
    """HAVING on an aggregate cannot move to WHERE; the database refuses it. Named 400."""
    sql = (
        "SELECT txn_type, SUM(quantity_kg) AS total FROM transactions "
        "GROUP BY txn_type HAVING SUM(quantity_kg) > 0"
    )
    answer = _ladder_answer(armed_openvault, dms_http, sql)

    resp = drill_client.post(
        "/v1/contract/drillthrough", json={"token": answer["drillthrough_token"]}
    )

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "drillthrough_error"
    assert detail["message"]


def test_drill_refused_by_the_manifest_is_a_named_4xx(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest refusal on the drill SQL keeps its own code and status."""
    from CortexOS.execution import submit
    from CortexOS.execution.manifest import PathNotAllowed

    answer = _ladder_answer(armed_openvault, dms_http, GROUP_COUNT_SQL)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise PathNotAllowed("table not granted to this session")

    monkeypatch.setattr(submit, "enforce_manifest", refuse)
    resp = drill_client.post(
        "/v1/contract/drillthrough", json={"token": answer["drillthrough_token"]}
    )

    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "path_not_allowed"
    assert detail["message"] == "table not granted to this session"


def _assert_named_400(drill_client, answer: dict[str, Any], reason: str) -> None:
    resp = drill_client.post(
        "/v1/contract/drillthrough", json={"token": answer["drillthrough_token"]}
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "drillthrough_error"
    assert reason in detail["message"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT txn_type, COUNT(*) AS n FROM transactions WHERE txn_type <> 'outbound' "
        "GROUP BY txn_type ORDER BY n DESC LIMIT 1",
        "SELECT txn_type, AVG(quantity_kg) AS a FROM transactions WHERE txn_type <> 'outbound' "
        "GROUP BY txn_type ORDER BY a DESC LIMIT 1",
        "SELECT txn_type, COUNT(*) AS n FROM transactions WHERE txn_type <> 'outbound' "
        "GROUP BY txn_type ORDER BY n DESC LIMIT 2 OFFSET 1",
    ],
    ids=["top1-count", "top1-avg", "offset"],
)
def test_top_n_group_answer_is_a_named_4xx_not_every_group(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
    sql: str,
) -> None:
    """The answer kept some groups only; a drill over every group would serve rows it never used."""
    answer = _ladder_answer(armed_openvault, dms_http, sql)
    kept = _oracle(sql)
    assert len(answer["rows"]) == len(kept) < 3
    for row in answer["rows"]:
        assert f"txn_type={row['txn_type']}" in answer["answer"]

    _assert_named_400(drill_client, answer, "keeps only some groups")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ROUND(SUM(quantity_kg), 2) AS total FROM transactions WHERE txn_type = 'inbound'",
        "SELECT SUM(quantity_kg) / COUNT(*) AS avgq FROM transactions WHERE txn_type = 'inbound'",
        "SELECT COUNT(*) FILTER (WHERE txn_type = 'inbound') AS n FROM transactions",
    ],
    ids=["round-sum", "sum-over-count", "count-filter"],
)
def test_nested_or_filtered_aggregate_answer_is_a_named_4xx(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
    sql: str,
) -> None:
    """An aggregate left inside an expression would come back as the answer value, not rows."""
    answer = _ladder_answer(armed_openvault, dms_http, sql)
    oracle = _oracle(sql)
    assert len(answer["rows"]) == 1
    col = next(iter(oracle[0]))
    assert abs(float(answer["rows"][0][col]) - float(oracle[0][col])) < 1e-6

    _assert_named_400(drill_client, answer, "aggregate inside an expression")


def test_top_n_row_answer_drills_to_exactly_its_rows(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
) -> None:
    """Positive control: a top-N answer without aggregates drills to the same N rows."""
    sql = (
        "SELECT txn_id, quantity_kg FROM transactions WHERE txn_type = 'inbound' "
        "ORDER BY quantity_kg DESC, txn_id LIMIT 5"
    )
    answer = _ladder_answer(armed_openvault, dms_http, sql)
    oracle = _oracle(sql)
    assert [r["txn_id"] for r in answer["rows"]] == [r["txn_id"] for r in oracle]

    resp = drill_client.post(
        "/v1/contract/drillthrough", json={"token": answer["drillthrough_token"]}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(r["txn_id"], float(r["quantity_kg"])) for r in body["rows"]] == [
        (r["txn_id"], float(r["quantity_kg"])) for r in oracle
    ]
    assert body["row_count"] == body["total_count"] == 5
    assert body["approximate"] is False


def test_group_answer_whose_limit_keeps_every_group_still_drills(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
) -> None:
    """Positive control: LIMIT 3 over exactly three groups dropped nothing, so rows come back."""
    sql = (
        "SELECT txn_type, COUNT(*) AS n FROM transactions WHERE txn_type <> 'outbound' "
        "GROUP BY txn_type ORDER BY n DESC LIMIT 3"
    )
    answer = _ladder_answer(armed_openvault, dms_http, sql)
    counts = {r["txn_type"]: int(r["n"]) for r in answer["rows"]}
    assert counts == {r["txn_type"]: int(r["n"]) for r in _oracle(sql)}
    assert len(counts) == 3
    for txn_type, n in counts.items():
        assert f"txn_type={txn_type}, n={n}" in answer["answer"]

    resp = drill_client.post(
        "/v1/contract/drillthrough", json={"token": answer["drillthrough_token"]}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    drilled: dict[str, int] = {}
    for row in body["rows"]:
        drilled[row["txn_type"]] = drilled.get(row["txn_type"], 0) + 1
    assert drilled == counts
    assert body["total_count"] == body["row_count"] == sum(counts.values())
    assert body["approximate"] is False


def _drill(drill_client, answer: dict[str, Any]) -> dict[str, Any]:
    resp = drill_client.post(
        "/v1/contract/drillthrough", json={"token": answer["drillthrough_token"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer_id"] == answer["answer_id"]
    assert body["row_count"] == len(body["rows"]) == body["total_count"]
    assert body["approximate"] is False
    return body


def test_ordered_string_agg_answer_drills_to_its_rows_in_the_aggregate_order(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
) -> None:
    """STRING_AGG(x, sep ORDER BY x): the drill lists the rows in the order the answer joined them."""
    sql = (
        "SELECT txn_type, STRING_AGG(txn_id, ', ' ORDER BY txn_id) AS ids FROM transactions "
        "WHERE txn_type = 'WRITE_OFF' GROUP BY txn_type"
    )
    answer = _ladder_answer(armed_openvault, dms_http, sql)
    oracle = _oracle(sql)
    assert answer["rows"] == oracle
    ids = oracle[0]["ids"].split(", ")
    assert f"txn_type=WRITE_OFF, ids={ids[0]}" in answer["answer"]

    body = _drill(drill_client, answer)

    assert [r["ids"] for r in body["rows"]] == ids
    assert {r["txn_type"] for r in body["rows"]} == {"WRITE_OFF"}


def test_ordered_array_agg_answer_drills_to_its_rows_in_the_aggregate_order(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
) -> None:
    sql = (
        "SELECT ARRAY_AGG(txn_id ORDER BY quantity_kg DESC, txn_id) AS ids FROM transactions "
        "WHERE txn_type = 'WRITE_OFF'"
    )
    answer = _ladder_answer(armed_openvault, dms_http, sql)
    ids = list(_oracle(sql)[0]["ids"])
    assert list(answer["rows"][0]["ids"]) == ids
    assert ids[0] in answer["answer"]

    body = _drill(drill_client, answer)

    assert [r["ids"] for r in body["rows"]] == ids


@pytest.mark.parametrize(
    ("sql", "first_is_answer"),
    [
        (
            "SELECT FIRST(txn_id ORDER BY quantity_kg DESC, txn_id) AS v FROM transactions "
            "WHERE txn_type = 'inbound'",
            True,
        ),
        ("SELECT ANY_VALUE(txn_id) AS v FROM transactions WHERE txn_type = 'inbound'", False),
        (
            "SELECT FIRST(txn_id IGNORE NULLS) AS v FROM transactions WHERE txn_type = 'inbound'",
            False,
        ),
    ],
    ids=["first-order-by", "any-value", "first-ignore-nulls"],
)
def test_pick_one_aggregate_answer_drills_to_the_rows_it_picked_from(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
    sql: str,
    first_is_answer: bool,
) -> None:
    """FIRST(x ORDER BY y), ANY_VALUE(x), FIRST(x IGNORE NULLS) drill to the rows they chose among."""
    answer = _ladder_answer(armed_openvault, dms_http, sql)
    picked = answer["rows"][0]["v"]
    assert f"v = {picked}" in answer["answer"]
    candidates = [
        r["txn_id"] for r in _oracle("SELECT txn_id FROM transactions WHERE txn_type = 'inbound'")
    ]
    assert picked in candidates
    if first_is_answer:
        assert picked == _oracle(sql)[0]["v"]

    body = _drill(drill_client, answer)

    assert sorted(r["v"] for r in body["rows"]) == sorted(candidates)
    if first_is_answer:
        assert body["rows"][0]["v"] == picked


def test_engine_aggregate_sqlglot_does_not_know_drills_to_rows_not_its_value(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
) -> None:
    """MEAN(x) is a DuckDB aggregate; the drill returns the rows it averaged, not one mean row."""
    sql = "SELECT MEAN(quantity_kg) AS m FROM transactions WHERE txn_type = 'WRITE_OFF'"
    answer = _ladder_answer(armed_openvault, dms_http, sql)
    mean = float(_oracle(sql)[0]["m"])
    assert abs(float(answer["rows"][0]["m"]) - mean) < 1e-9
    assert "m = " in answer["answer"]
    rows = _oracle("SELECT quantity_kg FROM transactions WHERE txn_type = 'WRITE_OFF'")

    body = _drill(drill_client, answer)

    assert body["row_count"] == len(rows) > 1
    assert sorted(float(r["m"]) for r in body["rows"]) == sorted(
        float(r["quantity_kg"]) for r in rows
    )
    assert abs(sum(float(r["m"]) for r in body["rows"]) / body["row_count"] - mean) < 1e-9


def test_grouped_engine_aggregate_answer_drills_per_group(
    armed_openvault,
    dms_http,  # noqa: F811
    drill_client,
) -> None:
    sql = "SELECT txn_type, MEAN(quantity_kg) AS m FROM transactions WHERE txn_type <> 'outbound' GROUP BY txn_type"
    answer = _ladder_answer(armed_openvault, dms_http, sql)
    means = {r["txn_type"]: float(r["m"]) for r in _oracle(sql)}
    assert {r["txn_type"]: float(r["m"]) for r in answer["rows"]} == means
    for txn_type in means:
        assert f"txn_type={txn_type}" in answer["answer"]

    body = _drill(drill_client, answer)

    per_group: dict[str, list[float]] = {}
    for row in body["rows"]:
        per_group.setdefault(row["txn_type"], []).append(float(row["m"]))
    assert set(per_group) == set(means)
    for txn_type, values in per_group.items():
        assert abs(sum(values) / len(values) - means[txn_type]) < 1e-9
