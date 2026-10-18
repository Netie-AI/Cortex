"""LITERAL-GROUNDING-01: the model may filter only on identifiers the question names.

Measured with a live model on bench/heldout: asked "How many kilograms of
SKU-ZZZZZ are sitting in LOC-999", the plan+SQL ladder filtered on a SKU and a
location the sampled values offered (SKU-00026, LOC-001) and served their total
as the answer. Every gate passed: the SQL was valid and granted. The ladder must
feed that back and regenerate, and never serve the substituted entity.
Assertions are on the envelope DMS receives (CLAUDE.md section 8). The model is
the scripted OpenVault.
"""

# ruff: noqa: F811  (pytest fixtures are imported, then requested by name)
from __future__ import annotations

import pytest

from CortexOS.dms import plan_sql_ask
from CortexOS.dms.sql_literal_grounding import ungrounded_literals
from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401
from tests.test_plan_sql.test_ai_sql_reads_table import _oracle, _rowset
from tests.test_plan_sql.test_c_loop_a_plan_sql import (  # noqa: F401
    GRANT,
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

# The ladder's fed-back code. Spelled out so the test fails on behaviour, not import.
LITERAL_UNGROUNDED = "PLAN_SQL_LITERAL_UNGROUNDED"


def _real_skus() -> list[str]:
    rows = _oracle(
        "SELECT DISTINCT sku FROM transactions WHERE sku LIKE 'SKU-%' ORDER BY sku LIMIT 2"
    )
    skus = [str(r["sku"]) for r in rows]
    assert len(skus) == 2, "the test lake must hold two SKU-coded transactions"
    return skus


def _kg_sql(sku: str) -> str:
    return f"SELECT SUM(quantity_kg) AS kg FROM transactions WHERE sku = '{sku}'"


def test_a_substituted_identifier_is_fed_back_and_never_served(
    armed_openvault, dms_http, execute_spy: list[str]
) -> None:
    named, other = _real_skus()
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, _kg_sql(other))
    _reply(armed_openvault, _kg_sql(named))
    dms_http.bind_session(SESSION, GRANT)
    oracle = _oracle(_kg_sql(named))

    body = _json(_ask(dms_http, question=f"How many kilograms of {named} moved in total?"))

    assert _rowset(body["rows"]) == _rowset(oracle)
    assert f"Result: kg = {body['rows'][0]['kg']}" in body["answer"], body["answer"]
    assert body["sql_used"].startswith(_kg_sql(named))
    # The substituted SQL never ran; only the one on the named SKU did.
    assert _ran(execute_spy) == [body["sql_used"]]
    retry = armed_openvault.chat_calls[2]["body"]["messages"][1]["content"]
    assert LITERAL_UNGROUNDED in retry
    assert f"LITERAL_NOT_IN_QUESTION:{other}" in retry
    _assert_rung(body, plan_sql_ask.rung_retry(1))


def test_an_entity_the_data_lacks_reconfirms_instead_of_answering_for_another(
    armed_openvault, dms_http, execute_spy: list[str]
) -> None:
    """The heldout case: the model keeps substituting; nothing is served."""
    named, _ = _real_skus()
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, _kg_sql(named))
    _reply(armed_openvault, _kg_sql(named))
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, question="How many kilograms of SKU-ZZZZZ moved in total?"))

    assert body["rows"] in ([], None)
    assert body["sql_used"] is None
    assert body["answer"].startswith("Reconfirm:"), body["answer"]
    assert named not in body["answer"]
    assert plan_sql_ask.LITERAL_UNGROUNDED == LITERAL_UNGROUNDED
    assert LITERAL_UNGROUNDED in " ".join(body["assumptions"]), body["assumptions"]
    assert _ran(execute_spy) == []
    _assert_rung(body, plan_sql_ask.RUNG_RECONFIRM)


def test_the_named_identifier_is_served_on_the_first_write(
    armed_openvault, dms_http, execute_spy: list[str]
) -> None:
    """Positive control: a grounded identifier is not delayed by the check."""
    named, _ = _real_skus()
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, _kg_sql(named))
    dms_http.bind_session(SESSION, GRANT)
    oracle = _oracle(_kg_sql(named))

    body = _json(_ask(dms_http, question=f"How many kilograms of {named} moved in total?"))

    assert _rowset(body["rows"]) == _rowset(oracle)
    assert body["sql_used"].startswith(_kg_sql(named))
    assert len(armed_openvault.chat_calls) == 2
    _assert_rung(body, plan_sql_ask.RUNG_PLAN)


@pytest.mark.parametrize(
    ("question", "sql"),
    [
        pytest.param(
            "total outbound kg sold",
            "SELECT SUM(quantity_kg) FROM t WHERE txn_type = 'outbound'",
            id="synonym",
        ),
        pytest.param(
            "stock of BETA",
            "SELECT * FROM inventory WHERE sku = 'SKU-BETA'",
            id="value-normalization",
        ),
        pytest.param(
            "kg moved in February 2012", "SELECT * FROM t WHERE ym = '201202'", id="period-code"
        ),
        pytest.param("orders since 2024", "SELECT * FROM t WHERE d >= '2024-01-01'", id="date"),
        pytest.param(
            "SKU 26 in LOC 1",
            "SELECT * FROM t WHERE sku = 'SKU-00026' AND loc = 'LOC-001'",
            id="zero-padded",
        ),
        pytest.param(
            "delayed shipments", "SELECT * FROM shipments WHERE status = 'DELAYED'", id="enum"
        ),
    ],
)
def test_legitimate_filters_pass(question: str, sql: str) -> None:
    assert ungrounded_literals(question, sql) == []


@pytest.mark.parametrize(
    ("question", "sql", "flagged"),
    [
        pytest.param(
            "How many kilograms of SKU-ZZZZZ are sitting in LOC-999 right now?",
            "SELECT SUM(quantity_kg) FROM inventory WHERE sku = 'SKU-00026' AND location_id = 'LOC-001'",
            ["LITERAL_NOT_IN_QUESTION:SKU-00026", "LITERAL_NOT_IN_QUESTION:LOC-001"],
            id="heldout-substitution",
        ),
        pytest.param(
            "Which supplier is late?",
            "SELECT * FROM suppliers WHERE supplier_id IN ('SUP-004', 'SUP-005')",
            ["LITERAL_NOT_IN_QUESTION:SUP-004", "LITERAL_NOT_IN_QUESTION:SUP-005"],
            id="in-list",
        ),
        pytest.param(
            "orders for customer 38508",
            "SELECT * FROM orders WHERE customer_id = '38509'",
            ["LITERAL_NOT_IN_QUESTION:38509"],
            id="long-number",
        ),
    ],
)
def test_an_identifier_the_question_never_named_is_flagged(
    question: str, sql: str, flagged: list[str]
) -> None:
    assert ungrounded_literals(question, sql) == flagged
