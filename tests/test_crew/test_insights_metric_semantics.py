"""METRIC-SEMANTICS-01: declared measure meaning reaches the /v1/insights SQL prompt as data.

The DMS ontology declares what a sales measure is (expression, the txn_type
filter it always carries, grain). The /v1/insights SQL-writing prompt carries
those definitions for the tables in play, and SQL that aggregates a declared
measure's columns without its required filter is fed back to the model as a
named retry inside the existing horizon; it is never returned as the answer.

Cortex returns SQL here and does not execute it, so every test goes through
``POST /v1/insights`` and asserts the envelope fields and the rows the returned
SQL gives on a test lake built through the declared value mapping
(CLAUDE.md section 8).
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import cot_climb, insights
from CortexOS.insights.caller_ontology import CallerCatalog
from CortexOS.ontology import value_domain as vd
from CortexOS.ontology.registry import pack_dir_for
from packs.dms.security.rate_limit import reset_limiter

SALES_ASK = "What is the total sales value in MYR?"
STOCK_ASK = "Stock value per location"
INBOUND_ASK = "Total inbound kg per location"

# Rows as the source system writes them; the lake build maps OUT/IN.
TXN_ROWS = (
    ("T1", "K1", "L1", "OUT", 5.0, 2.5),
    ("T2", "K2", "L1", "OUT", 1.0, 10.0),
    ("T3", "K3", "L2", "IN", 70.0, 3.0),
    ("T4", "K4", "L2", "OUT", 2.0, 100.0),
    ("T5", "K5", "L3", "ADJUST", 9.0, 1.0),
)
INV_ROWS = (
    ("K1", "L1", 10.0, 2.0),
    ("K2", "L1", 4.0, 5.0),
    ("K3", "L2", 3.0, 7.0),
)

# Oracles computed in Python from the rows above, not from SQL.
ORACLE_SALES = round(sum(q * c for _, _, _, t, q, c in TXN_ROWS if t == "OUT"), 2)
UNFILTERED = round(sum(q * c for _, _, _, _, q, c in TXN_ROWS), 2)
ORACLE_STOCK = sorted(
    (loc, sum(q * c for _, l2, q, c in INV_ROWS if l2 == loc)) for loc in {r[1] for r in INV_ROWS}
)
ORACLE_INBOUND = sorted(
    (loc, sum(q for _, _, l2, t, q, _ in TXN_ROWS if l2 == loc and t == "IN"))
    for loc in {r[2] for r in TXN_ROWS if r[3] == "IN"}
)

GOOD_SQL = (
    "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS sales_value_myr "
    "FROM transactions WHERE txn_type = 'outbound'"
)
OMITTED_SQL = (
    "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS sales_value_myr FROM transactions"
)
INVERTED_SQL = (
    "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS sales_value_myr "
    "FROM transactions WHERE txn_type <> 'outbound'"
)
# #402 / #235 review: the source spelling, which a mapped lake no longer stores.
SOURCE_SPELLED_SQL = (
    "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS sales_value_myr "
    "FROM transactions WHERE txn_type = 'OUT'"
)
STOCK_SQL = (
    "SELECT location_id, SUM(quantity_kg * unit_cost_myr) AS stock_value_myr "
    "FROM inventory GROUP BY location_id ORDER BY location_id"
)
INBOUND_SQL = (
    "SELECT location_id, SUM(quantity_kg) AS inbound_kg FROM transactions "
    "WHERE txn_type = 'inbound' GROUP BY location_id ORDER BY location_id"
)


def _script(sqls: list[str], prompts: list[tuple[str, str]]) -> Any:
    """A stub model that writes the next scripted SQL on each generative_ask call."""
    queue = list(sqls)

    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        prompts.append((purpose, prompt or ""))
        if purpose == "generative_ask":
            sql = queue.pop(0) if len(queue) > 1 else queue[0]
            return {
                "ok": True,
                "text": sql,
                "identity": "cortex:crew:generative-ask",
                "route": {"label": "stub", "model": "stub"},
            }
        return {
            "ok": True,
            "text": "Use transactions. No numbers.",
            "identity": "cortex:crew:think",
        }

    return fake


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> TestClient:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    return TestClient(
        create_app(), client=("127.0.0.1", 5555), headers={"X-API-Key": "dms-demo-admin-key"}
    )


def _rows(sql: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            "CREATE TABLE transactions (txn_id VARCHAR, sku VARCHAR, location_id VARCHAR, "
            "txn_type VARCHAR, quantity_kg DOUBLE, unit_cost_myr DOUBLE)"
        )
        con.executemany("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?)", list(TXN_ROWS))
        con.execute(
            "CREATE TABLE inventory (sku VARCHAR, location_id VARCHAR, "
            "quantity_kg DOUBLE, unit_cost_myr DOUBLE)"
        )
        con.executemany("INSERT INTO inventory VALUES (?, ?, ?, ?)", list(INV_ROWS))
        vd.apply_to_lake(con, vd.load_value_domains(pack_dir_for("dms")))
        return [tuple(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def _ask(client: TestClient, intent: str) -> dict[str, Any]:
    res = client.post("/v1/insights", json={"intent": intent, "ask": False, "generate": True})
    assert res.status_code == 200, res.text
    return res.json()


def _gen_prompts(prompts: list[tuple[str, str]]) -> list[str]:
    return [p for purpose, p in prompts if purpose == "generative_ask"]


def _served(body: dict[str, Any]) -> str:
    """The SQL the customer receives, after the envelope invariants every serve must meet."""
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert body["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert body["generative"]["ok"] is True
    assert body["generative"]["valid"] is True
    assert not str(body["generative"]["check"]).startswith("certified_query")
    sql = str(body["query_sql"])
    assert body["sql_used"] == sql
    return sql


SALES_LINE = (
    "measure transactions.sales_value_myr = SUM(quantity_kg * unit_cost_myr) "
    "WHERE transactions.txn_type = 'outbound'; grain: one transactions row (txn_id)"
)


# ── the definitions reach the prompt, selected by the tables in play ─────────


def test_sql_prompt_carries_declared_measure_definitions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import freeroute as fr

    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(fr, "complete", _script([GOOD_SQL], prompts))
    body = _ask(client, SALES_ASK)

    gen = _gen_prompts(prompts)
    assert len(gen) == 1, prompts
    assert SALES_LINE in gen[0]
    assert "measure transactions.sales_volume_kg = SUM(quantity_kg)" in gen[0]
    # The ranking echoed on the wire is unchanged: definitions live in the prompt.
    txn = next(r for r in body["ontology"]["locations"] if r["id"] == "transactions")
    assert set(txn["where"]) == {"table", "primary_key", "columns"}

    sql = _served(body)
    assert sql.startswith(GOOD_SQL)  # the validator may append a LIMIT
    assert _rows(sql) == [(ORACLE_SALES,)]
    text = insights.render_tool_text(body)
    assert "sql_valid: True" in text
    assert "txn_type = 'outbound'" in text


def test_definitions_follow_tables_not_question_words() -> None:
    txn = {"id": "transactions", "where": {"table": "transactions", "columns": ["txn_id"]}}
    inv = {"id": "inventory", "where": {"table": "inventory", "columns": ["sku"]}}
    sales_words = "\n".join(
        cot_climb._ontology_lines({"intent": "total sales revenue", "locations": [inv]})
    )
    assert "measure " not in sales_words
    plain = "\n".join(cot_climb._ontology_lines({"intent": "rows", "locations": [txn]}))
    assert SALES_LINE in plain
    # A table reached through a ranked metric is in play too.
    via_metric = {
        "intent": "rows",
        "locations": [inv],
        "metrics": [{"id": "m", "where": {"tables": ["transactions"]}}],
    }
    assert SALES_LINE in "\n".join(cot_climb._ontology_lines(via_metric))


def test_caller_catalog_gets_no_pack_measures_and_no_pack_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )
    caller = CallerCatalog(
        tables={"transactions": ["txn_type", "quantity_kg", "unit_cost_myr"]},
        scores={},
        measures={},
        links=[],
    )
    ranking = insights.ranking_from_caller(SALES_ASK, caller)
    assert "measure " not in "\n".join(cot_climb._ontology_lines(ranking))
    prompts: list[tuple[str, str]] = []
    out = asyncio.run(cot_climb.climb(SALES_ASK, ranking, complete=_script([OMITTED_SQL], prompts)))
    # The caller's schema is the caller's law; the pack's measures do not apply.
    assert out["ok"] is True, out
    assert len(_gen_prompts(prompts)) == 1
    assert _rows(str(out["sql"])) == [(UNFILTERED,)]


# ── the structural check feeds a named retry; a violation is never served ───


@pytest.mark.parametrize(
    ("bad_sql", "kind"),
    [
        (OMITTED_SQL, "measure_filter_missing"),
        (INVERTED_SQL, "measure_filter_inverted"),
        (SOURCE_SPELLED_SQL, "measure_filter_undeclared"),
    ],
)
def test_sql_breaking_a_declared_measure_is_retried_then_corrected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, bad_sql: str, kind: str
) -> None:
    from CortexOS.crew import freeroute as fr

    assert _rows(bad_sql) != [(ORACLE_SALES,)]  # the bad SQL would be a wrong answer
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(fr, "complete", _script([bad_sql, GOOD_SQL], prompts))
    body = _ask(client, SALES_ASK)

    gen = _gen_prompts(prompts)
    assert len(gen) == 2, prompts
    # The second SQL prompt names the violation and the declared definition.
    assert kind + ":sales_value_myr" in gen[1]
    assert "transactions.txn_type = 'outbound'" in gen[1]
    assert kind not in gen[0]

    sql = _served(body)
    assert sql.startswith(GOOD_SQL)  # the validator may append a LIMIT
    assert _rows(sql) == [(ORACLE_SALES,)]
    steps = [s for s in body["generative"]["climb"]["steps"] if s["kind"] == "generate"]
    assert [s["ok"] for s in steps] == [False, True]
    assert steps[0]["reason"].startswith(kind + ":sales_value_myr")
    assert body["generative"]["climb"]["attempts"] == 2
    text = insights.render_tool_text(body)
    assert "sql_valid: True" in text
    assert "txn_type = 'outbound'" in text


def test_sql_that_never_applies_the_filter_is_refused_not_served(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import freeroute as fr

    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(fr, "complete", _script([OMITTED_SQL], prompts))
    body = _ask(client, SALES_ASK)

    assert len(_gen_prompts(prompts)) == cot_climb.HORIZON
    assert body["generative"]["ok"] is False
    assert body["generative"]["sql"] is None
    assert body.get("sql_used") is None
    assert "query_sql" not in body
    assert body["values"] == []
    assert body["badge"] != "green"
    assert "measure_filter_missing:sales_value_myr" in body["generative"]["refuse_reason"]
    text = insights.render_tool_text(body)
    assert "measure_filter_missing:sales_value_myr" in text
    assert str(UNFILTERED) not in text


def test_sql_that_keeps_pinning_an_undeclared_value_is_refused_not_served(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#235 review: ``txn_type = 'OUT'`` on a mapped lake sums no rows; it is never served."""
    from CortexOS.crew import freeroute as fr

    assert _rows(SOURCE_SPELLED_SQL) == [(None,)]  # the silent empty it would serve
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(fr, "complete", _script([SOURCE_SPELLED_SQL], prompts))
    body = _ask(client, SALES_ASK)

    gen = _gen_prompts(prompts)
    assert len(gen) == cot_climb.HORIZON
    # Every retry prompt names the violation and the declared values to use.
    for prompt in gen[1:]:
        assert "measure_filter_undeclared:sales_value_myr" in prompt
        assert "'outbound', 'inbound', 'ADJUST', 'WRITE_OFF'" in prompt
    assert body["generative"]["ok"] is False
    assert body["generative"]["sql"] is None
    assert body.get("sql_used") is None
    assert "query_sql" not in body
    assert body["values"] == []
    assert body["badge"] != "green"
    assert "measure_filter_undeclared:sales_value_myr" in body["generative"]["refuse_reason"]
    text = insights.render_tool_text(body)
    assert "measure_filter_undeclared:sales_value_myr" in text
    assert "txn_type = 'OUT'" not in text


# ── positive controls ────────────────────────────────────────────────────────


def test_table_without_a_declared_measure_is_unaffected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """inventory also has quantity_kg * unit_cost_myr; no measure is declared there."""
    from CortexOS.crew import freeroute as fr

    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(fr, "complete", _script([STOCK_SQL], prompts))
    body = _ask(client, STOCK_ASK)

    assert len(_gen_prompts(prompts)) == 1
    sql = _served(body)
    assert sql.startswith(STOCK_SQL)
    assert _rows(sql) == ORACLE_STOCK
    assert "sql_valid: True" in insights.render_tool_text(body)


def test_explicitly_pinned_other_movement_kind_is_served_first_time(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import freeroute as fr

    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(fr, "complete", _script([INBOUND_SQL], prompts))
    body = _ask(client, INBOUND_ASK)

    assert len(_gen_prompts(prompts)) == 1
    sql = _served(body)
    assert _rows(sql) == ORACLE_INBOUND
    assert "sql_valid: True" in insights.render_tool_text(body)
