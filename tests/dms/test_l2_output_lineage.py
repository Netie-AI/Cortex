"""AI-SQL-READS-TABLE-01 on the L2 serve: a literal answer column is refused.

Drives the real DMS L2 port through ``attempt_l2`` and ``answer()`` (and the
contract route) against the scripted OpenVault. A model candidate whose output
columns do not derive from a granted table is refused by the L2 gate and fed
back; it never reaches execution or the customer. Assertions are on the
rendered answer text and rows, checked against an oracle run on the test lake
(CLAUDE.md section 8).
"""

# ruff: noqa: F811  (pytest fixtures are imported, then requested by name)
from __future__ import annotations

from decimal import Decimal
from typing import Any

import duckdb
import pytest

from tests.dms.test_c7_02_manifest_before_explain import _badge, dms_http  # noqa: F401
from tests.dms.test_l2_freeroute_envelope import (  # noqa: F401
    QUESTION,
    _ask,
    _l2_real_port,
    _reply_with_served_stamp,
)

LITERAL = "424242"
# The gate's violation token. Spelled out so the test fails on behaviour, not import.
NOT_FROM_TABLE = "OUTPUT_NOT_FROM_TABLE"
GOOD_SQL = "SELECT sku FROM inventory ORDER BY sku LIMIT 5"
PROVIDER = "groq"
MODEL = "openai/gpt-oss-120b"

LITERAL_SQL = [
    pytest.param(
        f"SELECT {LITERAL} AS n FROM inventory WHERE sku IS NOT NULL LIMIT 1",
        "n",
        id="constant-filtered-by-column",
    ),
    pytest.param(
        f"SELECT sku, {LITERAL} AS n FROM inventory ORDER BY sku LIMIT 5",
        "n",
        id="constant-beside-column",
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


def _assert_literal_absent(env: dict[str, Any]) -> None:
    assert LITERAL not in (env.get("answer") or ""), env.get("answer")
    assert all(LITERAL not in str(v) for row in env.get("rows") or [] for v in row.values())


def _assert_rendered(env: dict[str, Any]) -> None:
    rows = env["rows"]
    answer = env.get("answer") or ""
    if len(rows) == 1 and len(rows[0]) == 1:
        ((key, value),) = rows[0].items()
        assert f"Result: {key} = {value}" in answer, answer
        return
    for row in rows[:5]:
        for value in row.values():
            assert str(value) in answer, (value, answer)


def _feedback(fake) -> str:  # noqa: ANN001
    """The prompt of the second SQL call, where prior violations are fed back."""
    return str(fake.chat_calls[1]["body"]["messages"])


@pytest.mark.parametrize(("literal_sql", "column"), LITERAL_SQL)
def test_l2_literal_candidate_is_refused_then_the_table_answer_is_served(
    armed_openvault, literal_sql: str, column: str
) -> None:
    armed_openvault.reply(literal_sql)
    armed_openvault.reply(GOOD_SQL)
    oracle = _oracle(GOOD_SQL)
    assert oracle

    env = _ask()

    _assert_literal_absent(env)
    assert env["layer"] == "generated", env
    assert env["badge"] == "L2_VALIDATED"
    assert _rowset(env["rows"]) == _rowset(oracle)
    _assert_rendered(env)
    assert _rowset(_oracle(env["sql_used"])) == _rowset(oracle)
    assert len(armed_openvault.chat_calls) == 2
    assert f"{NOT_FROM_TABLE}:{column}" in _feedback(armed_openvault)


@pytest.mark.parametrize(("literal_sql", "column"), LITERAL_SQL)
def test_l2_literal_on_every_candidate_is_never_served(
    armed_openvault, literal_sql: str, column: str
) -> None:
    armed_openvault.default_content = literal_sql

    env = _ask()

    _assert_literal_absent(env)
    assert env["badge"] == "abstain", env
    assert env["rows"] == []
    assert env.get("sql_used") is None
    assert f"{NOT_FROM_TABLE}:{column}" in (env.get("answer") or "")
    assert 1 <= len(armed_openvault.chat_calls) <= 3


def test_contract_l2_literal_candidate_is_refused_then_served(
    armed_openvault,
    dms_http,
) -> None:
    literal_sql = LITERAL_SQL[0].values[0]
    _reply_with_served_stamp(armed_openvault, literal_sql, provider=PROVIDER, model=MODEL)
    _reply_with_served_stamp(armed_openvault, GOOD_SQL, provider=PROVIDER, model=MODEL)
    dms_http.bind_session("l2-lineage", {"inventory": "TRUE"})
    oracle = _oracle(GOOD_SQL)

    resp = dms_http.post(
        "/v1/contract/ask", json={"question": QUESTION, "session_id": "l2-lineage"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_literal_absent(body)
    # The contract wire carries the generated layer as the query_skill badge.
    assert _badge(body) == "query_skill", body
    assert body["provenance"]["layer"] == "generated", body["provenance"]
    assert _rowset(body["rows"]) == _rowset(oracle)
    _assert_rendered(body)
    assert _rowset(_oracle(body["sql_used"])) == _rowset(oracle)
    assert body["served_provider"] == PROVIDER and body["served_model"] == MODEL
    assert f"{NOT_FROM_TABLE}:n" in _feedback(armed_openvault)


def test_contract_l2_literal_only_is_not_served(armed_openvault, dms_http) -> None:
    literal_sql = LITERAL_SQL[0].values[0]
    for _ in range(3):
        _reply_with_served_stamp(armed_openvault, literal_sql, provider=PROVIDER, model=MODEL)
    dms_http.bind_session("l2-lineage-only", {"inventory": "TRUE"})

    resp = dms_http.post(
        "/v1/contract/ask", json={"question": QUESTION, "session_id": "l2-lineage-only"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_literal_absent(body)
    assert _badge(body).lower() == "abstain", body
    assert body.get("rows") in ([], None)
    assert body.get("sql_used") is None
    assert body.get("drillthrough_token") is None
    assert f"{NOT_FROM_TABLE}:n" in body["answer"]


POSITIVE = [
    pytest.param(GOOD_SQL, id="columns"),
    pytest.param(
        "SELECT sku, quantity_kg * 2 AS double_kg FROM inventory ORDER BY sku LIMIT 5",
        id="arithmetic",
    ),
    pytest.param(
        # Numeric branches: the DMS port's literal normaliser refuses unmatched
        # string literals before the gate, on base and after.
        "SELECT sku, CASE WHEN quantity_kg > reorder_level_kg THEN 1 ELSE 0 END "
        "AS above_reorder FROM inventory ORDER BY sku LIMIT 5",
        id="case",
    ),
    pytest.param("SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory", id="stored-aggregate"),
]


@pytest.mark.parametrize("sql", POSITIVE)
def test_l2_table_derived_candidate_is_served_first_time(armed_openvault, sql: str) -> None:
    armed_openvault.reply(sql)
    oracle = _oracle(sql)
    assert oracle

    env = _ask()

    assert env["badge"] == "L2_VALIDATED", env
    # A cut page appends the lot key, so the served rows are the oracle of
    # sql_used, not of the model's non-total ORDER BY.
    assert sql.split(" ORDER BY ")[0] in (env.get("sql_used") or "")
    assert _rowset(env["rows"]) == _rowset(_oracle(env["sql_used"]))
    _assert_rendered(env)
    assert len(armed_openvault.chat_calls) == 1
