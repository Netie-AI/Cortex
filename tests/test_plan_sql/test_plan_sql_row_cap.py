"""A served answer never carries more rows than its SQL's LIMIT, on the customer envelope.

Reproduced on DuckDB 2.0.0 through POST /v1/contract/ask: a reading
``... LEFT JOIN inventory ON ... AND inventory.quantity_kg < 0 LIMIT 5`` served
row_count 5001 with ``sql_used`` ending ``LIMIT 5``. The optimizer proves the
join side empty and drops the LIMIT operator. The gate's ``MAX_LIMIT`` cap is a
LIMIT too, so an unbounded statement passed it as well. ``pyproject.toml`` pins
``duckdb>=1.1,<2`` (tests/test_execution/test_duckdb_limit_pin.py holds the
engine shapes); these assert the envelope DMS receives: badge, answer text,
rows and row_count.
"""

# Fixtures imported from sibling modules are redefined as test parameters.
# ruff: noqa: F811

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from CortexOS.dms.sql_guardrail import MAX_LIMIT
from tests.test_plan_sql.test_c_loop_a_plan_sql import (  # noqa: F401
    PAYLOAD,
    SESSION,
    _ask,
    _json,
    _plan_sql_on,
    _ran,
    _reply,
    dms_http,
    execute_spy,
)

PAY: dict[str, Any] = {
    "tables": [
        *PAYLOAD["tables"],
        {
            "name": "inventory",
            "columns": [
                {"name": "sku", "type": "VARCHAR", "masked": False},
                {"name": "quantity_kg", "type": "DOUBLE", "masked": False},
            ],
        },
    ]
}
GRANT = {"transactions": "TRUE", "inventory": "TRUE"}
# No inventory row has a negative quantity: the optimizer proves the join side empty.
BASE = (
    "SELECT transactions.txn_type, transactions.sku FROM transactions LEFT JOIN inventory "
    "ON transactions.sku = inventory.sku AND inventory.quantity_kg < 0"
)


def _lake_rows(sql: str) -> list[tuple[Any, ...]]:
    """What the installed engine returns for ``sql`` on the served lake, no executor."""
    from CortexOS.execution import submit

    con = duckdb.connect(str(submit.DEFAULT_DB), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _served(armed_openvault, dms_http, sql: str) -> dict[str, Any]:
    _reply(armed_openvault, f"```sql\n{sql};\n```")
    dms_http.bind_session(SESSION, GRANT)
    return _json(_ask(dms_http, payload=PAY))


def _assert_envelope(body: dict[str, Any], limit: int, sql: str) -> None:
    assert body["provenance"]["badge"] == "session", body["answer"]
    rows = body["rows"] or []
    assert len(rows) == limit
    assert body["row_count"] == len(rows) == limit
    # The answer text counts the rows it was given, and shows only served rows.
    assert body["answer"].startswith(f"Found {limit} row(s)."), body["answer"]
    for row in rows[:5]:
        assert f"txn_type={row['txn_type']}, sku={row['sku']}" in body["answer"]
    if limit > 5:
        assert f"and {limit - 5} more rows" in body["answer"], body["answer"]
    assert body["sql_used"] == f"{BASE} LIMIT {limit}"
    assert f"(rows={limit})" in " ".join(body["assumptions"])
    # Every served row is a real row of the statement on the lake.
    truth = {tuple(r) for r in _lake_rows(BASE)}
    assert {(r["txn_type"], r["sku"]) for r in rows} <= truth
    assert body["drillthrough_token"]


def test_dropped_limit_is_not_served_past_the_limit(
    armed_openvault,
    dms_http,
    execute_spy: list[str],
) -> None:
    sql = f"{BASE} LIMIT 5"
    raw = _lake_rows(sql)
    if duckdb.__version__.startswith("2.0."):
        # The engine fact this guards: the plan lost the LIMIT on this lake.
        assert len(raw) > 5
    body = _served(armed_openvault, dms_http, sql)
    _assert_envelope(body, 5, sql)
    assert _ran(execute_spy) == [body["sql_used"]]


def test_gate_max_limit_cap_holds_when_the_plan_drops_it(
    armed_openvault,
    dms_http,
    execute_spy: list[str],
) -> None:
    """No LIMIT in the model SQL: the gate writes LIMIT MAX_LIMIT, the plan drops it."""
    assert len(_lake_rows(BASE)) > MAX_LIMIT
    body = _served(armed_openvault, dms_http, BASE)
    _assert_envelope(body, MAX_LIMIT, BASE)
    assert len(body["rows"]) <= MAX_LIMIT


@pytest.mark.parametrize("limit", [1, 7])
def test_small_limits_on_the_dropped_plan(
    armed_openvault,
    dms_http,
    limit: int,
) -> None:
    body = _served(armed_openvault, dms_http, f"{BASE} LIMIT {limit}")
    _assert_envelope(body, limit, f"{BASE} LIMIT {limit}")
