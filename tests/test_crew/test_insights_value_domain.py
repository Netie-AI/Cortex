"""VALUE-NORMALIZE-CX-01 (#387): /v1/insights generates against the declared txn_type values.

The model's generation context lists the canonical values the ontology
declares, and SQL written with them returns the oracle rows on a lake built
through the same declaration. Every test goes through ``POST /v1/insights``
and asserts the envelope fields and the rows the returned SQL produces
(CLAUDE.md section 8; Cortex returns SQL here, it does not execute it).
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

ASK = "How many outbound transactions per location?"
MODEL_SQL = (
    "SELECT location_id, COUNT(*) AS n FROM transactions "
    "WHERE txn_type = 'outbound' GROUP BY location_id ORDER BY location_id"
)


def _stub(sql: str, prompts: list[tuple[str, str]]) -> Any:
    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        prompts.append((purpose, prompt or ""))
        if purpose == "generative_ask":
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


def _lake() -> duckdb.DuckDBPyConnection:
    """Source rows spelled as the source spells them; the declared mapping is the build step."""
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE transactions (txn_id VARCHAR, sku VARCHAR, location_id VARCHAR, "
        "txn_type VARCHAR, quantity_kg DOUBLE, unit_cost_myr DOUBLE)"
    )
    con.execute(
        "INSERT INTO transactions VALUES ('T1','K1','L1','OUT',5,2.5),('T2','K2','L1','OUT',1,10),"
        "('T3','K3','L2','IN',70,3),('T4','K4','L2','OUT',2,100),('T5','K5','L3','ADJUST',9,1)"
    )
    vd.apply_to_lake(con, vd.load_value_domains(pack_dir_for("dms")))
    return con


def _rows(sql: str) -> list[tuple[Any, ...]]:
    con = _lake()
    try:
        return [tuple(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def test_generation_context_lists_declared_values_without_a_wire_change() -> None:
    ranking = insights.retrieve_ontology(ASK)
    txn = next(r for r in ranking["locations"] if r["id"] == "transactions")
    # The ranking is echoed on /v1/insights: no new key there (#351 owns 1.6.0).
    assert set(txn["where"]) == {"table", "primary_key", "columns"}
    lines = "\n".join(cot_climb._ontology_lines(ranking))
    assert "transactions.txn_type values: 'outbound', 'inbound', 'ADJUST', 'WRITE_OFF'" in lines


def test_caller_catalog_is_not_given_the_pack_declaration() -> None:
    caller = CallerCatalog(
        tables={"transactions": ["txn_type", "sku"]}, scores={}, measures={}, links=[]
    )
    ranking = insights.ranking_from_caller(ASK, caller)
    assert any(r["id"] == "transactions" for r in ranking["locations"])
    lines = "\n".join(cot_climb._ontology_lines(ranking))
    assert "- transactions: txn_type, sku" in lines
    assert "values:" not in lines


def test_generated_sql_on_declared_values_returns_oracle_rows(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import freeroute as fr

    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(fr, "complete", _stub(MODEL_SQL, prompts))
    res = client.post("/v1/insights", json={"intent": ASK, "ask": False, "generate": True})
    assert res.status_code == 200, res.text
    body = res.json()

    # The model saw the canonical spelling, never the source one.
    gen = [p for purpose, p in prompts if purpose == "generative_ask"]
    assert gen, prompts
    assert "transactions.txn_type values: 'outbound', 'inbound'" in gen[0]
    assert "'OUT'" not in gen[0]

    # Envelope: validated SQL, no Cortex figure, model-written and says so.
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert body["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert body["generative"]["ok"] is True
    assert not str(body["generative"]["check"]).startswith("certified_query")
    sql = str(body["query_sql"])
    assert "'outbound'" in sql

    # Rows: the returned SQL on the declared lake equals the oracle.
    assert _rows(sql) == [("L1", 2), ("L2", 1)]
    text = insights.render_tool_text(body)
    assert "outbound" in text
