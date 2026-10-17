"""AI-SQL-READS-TABLE-01 on the plan-SQL ladder: an answer must come from a table.

Model-written SQL whose output columns are literals (a constant selected from
a granted table, from a VALUES list, or next to a real column) passes the
grant and allowlist checks. The ladder must feed that back and regenerate, and
never serve the literal. Assertions are on the envelope DMS receives: rendered
answer text and rows, checked against an oracle run directly on the test lake
(CLAUDE.md section 8). The model is the scripted OpenVault.
"""

# ruff: noqa: F811  (pytest fixtures are imported, then requested by name)
from __future__ import annotations

from decimal import Decimal
from typing import Any

import duckdb
import pytest

from CortexOS.dms import plan_sql_ask
from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401
from tests.test_plan_sql.test_c_loop_a_plan_sql import (  # noqa: F401
    GOOD_SQL,
    GRANT,
    PAYLOAD,
    PLAN_TEXT,
    SESSION,
    _ask,
    _assert_rung,
    _json,
    _plan_sql_on,
    _ran,
    _reply,
    execute_spy,
)

LITERAL = "424242"
# The ladder's fed-back code. Spelled out so the test fails on behaviour, not import.
NOT_FROM_TABLE = "PLAN_SQL_OUTPUT_NOT_FROM_TABLE"

LITERAL_SQL = [
    pytest.param(f"SELECT {LITERAL} AS n FROM transactions LIMIT 1", "n", id="constant-from-table"),
    pytest.param(f"SELECT {LITERAL} AS n FROM (VALUES (1))", "n", id="constant-from-values"),
    pytest.param(
        f"SELECT 'IN' AS txn_type, {LITERAL} AS n FROM transactions GROUP BY 1",
        "n",
        id="constants-grouped",
    ),
    pytest.param(
        f"SELECT txn_type, {LITERAL} AS n FROM transactions GROUP BY txn_type",
        "n",
        id="constant-beside-column",
    ),
    pytest.param(
        f"SELECT {LITERAL} AS n FROM transactions WHERE txn_type IS NOT NULL LIMIT 1",
        "n",
        id="constant-filtered-by-column",
    ),
]


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


def _assert_rendered(body: dict[str, Any]) -> None:
    """Every previewed row is in the rendered answer text, as the engine renders it."""
    rows = body["rows"]
    if len(rows) == 1 and len(rows[0]) == 1:
        ((key, value),) = rows[0].items()
        assert f"Result: {key} = {value}" in body["answer"], body["answer"]
        return
    for row in rows[:5]:
        line = ", ".join(f"{k}={v}" for k, v in row.items())
        assert line in body["answer"], (line, body["answer"])


def _assert_literal_absent(body: dict[str, Any]) -> None:
    assert LITERAL not in body["answer"], body["answer"]
    assert all(LITERAL not in str(v) for row in body["rows"] or [] for v in row.values())


@pytest.mark.parametrize(("literal_sql", "column"), LITERAL_SQL)
def test_literal_output_is_fed_back_then_the_table_answer_is_served(
    armed_openvault,
    dms_http,
    execute_spy: list[str],
    literal_sql: str,
    column: str,
) -> None:
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, literal_sql)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)
    oracle = _oracle(GOOD_SQL)
    assert oracle

    body = _json(_ask(dms_http))

    _assert_literal_absent(body)
    assert _rowset(body["rows"]) == _rowset(oracle)
    assert body["row_count"] == len(oracle)
    for row in oracle:
        assert f"txn_type={row['txn_type']}, n={row['n']}" in body["answer"]
    assert body["sql_used"].startswith(GOOD_SQL)
    assert _rowset(_oracle(body["sql_used"])) == _rowset(oracle)
    # The literal SQL never ran; only the table answer did.
    assert _ran(execute_spy) == [body["sql_used"]]
    assert all(LITERAL not in sql for sql in execute_spy)
    retry = armed_openvault.chat_calls[2]["body"]["messages"][1]["content"]
    assert NOT_FROM_TABLE in retry
    assert f"OUTPUT_NOT_FROM_TABLE:{column}" in retry
    assert len(armed_openvault.chat_calls) == 3
    _assert_rung(body, plan_sql_ask.rung_retry(1))
    gate_lines = [line for line in body["assumptions"] if line.startswith("step gate:")]
    assert any(NOT_FROM_TABLE in line for line in gate_lines)


def test_literal_output_on_every_write_reconfirms_and_serves_nothing(
    armed_openvault,
    dms_http,
    execute_spy: list[str],
) -> None:
    """No stronger route configured: the ladder reconfirms, it does not serve the literal."""
    literal_sql = f"SELECT {LITERAL} AS n FROM transactions LIMIT 1"
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, literal_sql)
    _reply(armed_openvault, literal_sql)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_literal_absent(body)
    assert body["rows"] in ([], None)
    assert body["sql_used"] is None
    assert body["answer"].startswith("Reconfirm:")
    assert plan_sql_ask.NOT_FROM_TABLE == NOT_FROM_TABLE
    assert NOT_FROM_TABLE in " ".join(body["assumptions"])
    assert _ran(execute_spy) == []
    _assert_rung(body, plan_sql_ask.RUNG_RECONFIRM)


JOIN_PAYLOAD: dict[str, Any] = {
    "tables": [
        PAYLOAD["tables"][0],
        {
            "name": "inventory",
            "columns": [
                {"name": "sku", "type": "VARCHAR"},
                {"name": "category", "type": "VARCHAR"},
                {"name": "quantity_kg", "type": "DOUBLE"},
            ],
        },
    ]
}

# CTEs and derived-table columns are refused by the SQL gate on this path
# before lineage is reached (UNKNOWN_TABLE / UNKNOWN_COLUMN); their lineage is
# covered in tests/dms/test_sql_lineage.py.
POSITIVE = [
    pytest.param("SELECT COUNT(*) AS n FROM transactions", PAYLOAD, id="count-star"),
    pytest.param(
        "SELECT txn_type, SUM(quantity_kg) AS kg, AVG(quantity_kg) AS avg_kg "
        "FROM transactions GROUP BY txn_type",
        PAYLOAD,
        id="aggregates-group-by",
    ),
    pytest.param(
        "SELECT txn_type, ROUND(SUM(quantity_kg) / 1000, 3) AS tonnes, COUNT(*) * 2 AS twice "
        "FROM transactions GROUP BY txn_type",
        PAYLOAD,
        id="arithmetic",
    ),
    pytest.param(
        "SELECT CASE WHEN quantity_kg >= 100 THEN 'large' ELSE 'small' END AS size, "
        "COUNT(*) AS n FROM transactions GROUP BY 1",
        PAYLOAD,
        id="case",
    ),
    pytest.param(
        "SELECT i.category, COUNT(*) AS n FROM transactions t "
        "JOIN inventory i ON i.sku = t.sku GROUP BY i.category",
        JOIN_PAYLOAD,
        id="join",
    ),
]


@pytest.mark.parametrize(("sql", "payload"), POSITIVE)
def test_table_derived_sql_is_still_served_first_time(
    armed_openvault,
    dms_http,
    execute_spy: list[str],
    sql: str,
    payload: dict[str, Any],
) -> None:
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, sql)
    grant = {t["name"]: "TRUE" for t in payload["tables"]}
    dms_http.bind_session(SESSION, grant)
    oracle = _oracle(sql)
    assert oracle

    body = _json(_ask(dms_http, payload=payload))

    assert _rowset(body["rows"]) == _rowset(oracle), body["answer"]
    assert _rowset(_oracle(body["sql_used"])) == _rowset(oracle)
    _assert_rendered(body)
    assert len(armed_openvault.chat_calls) == 2
    assert _ran(execute_spy) == [body["sql_used"]]
    _assert_rung(body, plan_sql_ask.RUNG_PLAN)


# A row count is an answer only when every relation it counts is a granted
# table. A VALUES list joined to a granted table makes it a count of typed rows.
VALUES_COUNT_SQL = [
    pytest.param(
        "SELECT COUNT(*) AS n FROM (VALUES (1),(2),(3),(4),(5),(6),(7)) AS v "
        "LEFT JOIN transactions ON FALSE",
        7,
        id="values-left-join-on-false",
    ),
]
# The variant that aliases the VALUES list with the table's own name is stopped
# by the manifest on this path; its lineage is in tests/dms/test_sql_lineage.py.


@pytest.mark.parametrize(("count_sql", "typed_count"), VALUES_COUNT_SQL)
def test_row_count_over_a_values_list_is_fed_back_then_the_table_answer_is_served(
    armed_openvault,
    dms_http,
    execute_spy: list[str],
    count_sql: str,
    typed_count: int,
) -> None:
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, count_sql)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)
    oracle = _oracle(GOOD_SQL)
    assert oracle
    # The typed count is not a count of the table.
    assert _oracle(count_sql) == [{"n": typed_count}]
    assert _oracle("SELECT COUNT(*) AS n FROM transactions")[0]["n"] != typed_count

    body = _json(_ask(dms_http))

    assert f"n = {typed_count}" not in body["answer"], body["answer"]
    assert {"n": typed_count} not in (body["rows"] or [])
    assert _rowset(body["rows"]) == _rowset(oracle)
    assert body["row_count"] == len(oracle)
    for row in oracle:
        assert f"txn_type={row['txn_type']}, n={row['n']}" in body["answer"]
    assert body["sql_used"].startswith(GOOD_SQL)
    assert _rowset(_oracle(body["sql_used"])) == _rowset(oracle)
    assert _ran(execute_spy) == [body["sql_used"]]
    assert all("VALUES" not in sql.upper() for sql in execute_spy)
    retry = armed_openvault.chat_calls[2]["body"]["messages"][1]["content"]
    assert NOT_FROM_TABLE in retry
    assert "OUTPUT_NOT_FROM_TABLE:n" in retry
    assert len(armed_openvault.chat_calls) == 3
    _assert_rung(body, plan_sql_ask.rung_retry(1))
