"""GEN-CERTIFIED-MEASURE-01: a certified measure that resolves the ask is served, not re-derived.

dms#231 F3: on cq_supplier_ranking the model wrote its own risk formula and
the static gate validated it, so DMS executed a plausible ranking built on an
invented weight. Now, when the ask resolves to a certified measure, Cortex
serves the certified SQL as stored (only a declared top-N may change) and does
not call the model; an ask the certified query cannot express abstains with
``certified_measure_cannot_express:<what>``. Every test goes through
``POST /v1/insights`` and asserts the envelope (status/badge/answer/values/
plan_source/served_reason) and the rows the returned SQL produces.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import cot_climb, insights
from packs.dms.security.rate_limit import reset_limiter
from tests.test_crew.conftest import FakeLLM

RANK_Q = "Rank suppliers by combined risk and lead time score"
INVENTED = (
    "SELECT supplier_id, ROUND(risk_score * 0.5 + lead_time_days * 0.5, 3) AS ranking_score "
    "FROM suppliers ORDER BY ranking_score DESC LIMIT 10"
)
CANNOT = "certified_measure_cannot_express"

# The six certified measures (outer SELECT computes arithmetic) and the ask
# that resolves to each one.
MEASURES = {
    "cq_supplier_ranking": RANK_Q,
    "cq_sales_top5_value": "Top 5 selling SKUs by revenue",
    "cq_spend_by_country": "What is our total spend by supplier country?",
    "cq_stock_value_by_category": "What is total stock value by category?",
    "cq_capacity_utilisation": "Show warehouse capacity utilisation",
    "cq_top3_category_sales": "show top 3 category sales",
}


def _stub(sql: str, prompts: list[str]) -> Any:
    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        prompts.append(f"{purpose}:{prompt or ''}")
        if purpose == "generative_ask":
            return {
                "ok": True,
                "text": sql,
                "identity": "cortex:crew:generative-ask",
                "route": {"label": "stub", "model": "stub"},
            }
        return {"ok": True, "text": "Use suppliers. No numbers.", "identity": "cortex:crew:think"}

    return fake


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> TestClient:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    # Loopback peer: the relay tier, so generate runs without a caller ov_ key.
    return TestClient(create_app(), client=("127.0.0.1", 5555))


@pytest.fixture
def crew_client(
    settings: Any, crew_env: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """POST /crew/insights: the HTTP route that carries a caller-typed query_plan."""
    from CortexOS.crew import freeroute as fr
    from CortexOS.crew.server import create_app

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )
    with TestClient(create_app(settings, llm_chat=FakeLLM()), client=("127.0.0.1", 5555)) as tc:
        yield tc


def _ask(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    q: str,
    sql: str,
    query_plan: dict[str, Any] | None = None,
) -> tuple[Any, list[str]]:
    from CortexOS.crew import freeroute as fr

    prompts: list[str] = []
    monkeypatch.setattr(fr, "complete", _stub(sql, prompts))
    body: dict[str, Any] = {"intent": q, "ask": False, "generate": True}
    path = "/v1/insights"
    if query_plan is not None:
        body["query_plan"] = query_plan
        path = "/crew/insights"  # /v1/insights takes no query_plan
    res = client.post(path, json=body)
    return res, prompts


def _lake() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE suppliers (supplier_id VARCHAR, supplier_name VARCHAR, country VARCHAR, "
        "risk_score DOUBLE, lead_time_days INTEGER)"
    )
    con.execute(
        "INSERT INTO suppliers VALUES ('S1','a','MY',0.9,10),('S2','b','SG',0.2,55),"
        "('S3','c','MY',0.5,30),('S4','d','TH',0.7,5),('S5','e','SG',0.1,60),('S6','f','MY',0.3,50)"
    )
    con.execute(
        "CREATE TABLE inventory (sku VARCHAR, category VARCHAR, supplier_id VARCHAR, "
        "location_id VARCHAR, quantity_kg DOUBLE, unit_cost_myr DOUBLE)"
    )
    con.execute(
        "INSERT INTO inventory VALUES ('K1','CHEM','S1','L1',10,2.5),('K2','FOOD','S2','L1',4,10),"
        "('K3','CHEM','S3','L2',7,3),('K4','FOOD','S4','L2',1,100),('K5','PACK','S5','L3',50,1),"
        "('K6','PACK','S6','L3',3,9),('K7','TOOL','S1','L1',2,4)"
    )
    con.execute(
        "CREATE TABLE transactions (txn_id VARCHAR, sku VARCHAR, location_id VARCHAR, "
        "txn_type VARCHAR, quantity_kg DOUBLE, unit_cost_myr DOUBLE)"
    )
    con.execute(
        "INSERT INTO transactions VALUES ('T1','K1','L1','OUT',5,2.5),('T2','K2','L1','OUT',1,10),"
        "('T3','K3','L2','IN',70,3),('T4','K4','L2','OUT',2,100),('T5','K5','L3','OUT',9,1),"
        "('T6','K6','L3','OUT',1,9),('T7','K1','L1','OUT',3,2.5),('T8','K7','L1','OUT',1,4)"
    )
    con.execute(
        "CREATE TABLE locations (location_id VARCHAR, location_code VARCHAR, "
        "current_load_kg DOUBLE, capacity_kg DOUBLE)"
    )
    con.execute(
        "INSERT INTO locations VALUES ('L1','A',333,1000),('L2','B',1,3),('L3','C',77.7,90)"
    )
    return con


def _rows(sql: str) -> list[tuple[Any, ...]]:
    con = _lake()
    try:
        return [tuple(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def _no_digits(answer: str, reason: str) -> bool:
    return not re.search(r"\d", answer.replace(reason, ""))


def _assert_served(body: dict[str, Any], mid: str, prompts: list[str]) -> str:
    # Validated, not executed in crew: DMS runs query_sql. No figure from Cortex here.
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert body["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert f"Served certified query {mid}" in body["answer"]
    assert body["served_provider"] is None and body["served_model"] is None
    assert body["served_reason"].startswith(f"certified_query:{mid}")
    assert body["generative"]["check"] == f"certified_query:{mid}"
    assert body["generative"]["route"] is None
    assert prompts == []  # the model was never called for this ask
    text = insights.render_tool_text(body)
    assert f"certified_query:{mid}" in text
    return str(body["query_sql"])


@pytest.mark.parametrize("mid", sorted(MEASURES))
def test_certified_measure_is_served_and_rows_equal_the_oracle(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, mid: str
) -> None:
    # The model would invent a formula; it is never asked.
    res, prompts = _ask(client, monkeypatch, MEASURES[mid], INVENTED)
    assert res.status_code == 200, res.text
    sql = _assert_served(res.json(), mid, prompts)
    oracle = cot_climb.gold_sql_for(mid)
    assert sql == oracle
    got, want = _rows(sql), _rows(oracle)
    assert got
    if "ORDER BY" in oracle.upper():
        assert got == want
    else:
        assert Counter(got) == Counter(want)


def test_invented_formula_is_not_what_dms_executes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dms#231 F3: the invented weight ranks differently; DMS gets the certified rows."""
    oracle = _rows(cot_climb.gold_sql_for("cq_supplier_ranking"))
    assert Counter(_rows(INVENTED)) != Counter(oracle)
    res, prompts = _ask(client, monkeypatch, RANK_Q.upper() + "?", INVENTED)
    assert res.status_code == 200, res.text
    sql = _assert_served(res.json(), "cq_supplier_ranking", prompts)
    assert _rows(sql) == oracle
    assert "0.65" in sql and "0.5 " not in sql  # in addition to the rows


@pytest.mark.parametrize(
    ("q", "what"),
    [
        (RANK_Q + " with equal weights", "terms(with equal weights)"),
        ("What is total stock value by category in warehouse A?", "terms(in warehouse a)"),
        ("Top 5 selling SKUs by revenue this month", "terms(this month)"),
        ("Show warehouse capacity utilisation for cold storage", "terms(for cold storage)"),
    ],
)
def test_extra_ask_the_certified_query_cannot_express_is_a_named_abstain(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, q: str, what: str
) -> None:
    res, prompts = _ask(client, monkeypatch, q, INVENTED)
    assert res.status_code == 200, res.text
    body = res.json()
    reason = f"{CANNOT}:{what}"
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert body["refuse_reason"] == reason
    assert reason in body["answer"]
    assert _no_digits(body["answer"], reason), body["answer"]
    assert "query_sql" not in body  # nothing for DMS to execute
    assert body["plan_source"] == insights.PLAN_SOURCE_OTHER
    assert any(u.get("why") == reason for u in body["validation"]["unsure"])
    assert prompts == []  # the model did not improvise the extra filter


@pytest.mark.parametrize(
    ("q", "plan", "what"),
    [
        (
            MEASURES["cq_stock_value_by_category"],
            {"measure": "stock_value_myr", "filters": [["location", "location_code", "WH-A"]]},
            "filter",
        ),
        (
            MEASURES["cq_stock_value_by_category"],
            {"measure": "stock_value_myr", "group_by": [["product", "sku"]]},
            "grain(sku)",
        ),
        (
            MEASURES["cq_capacity_utilisation"],
            {"measure": "utilisation_pct", "keep_gt": 90.0},
            "keep_gt",
        ),
        (
            MEASURES["cq_capacity_utilisation"],
            {"measure": "utilisation_pct", "limit": 2},
            "limit(2)",
        ),
        (
            MEASURES["cq_sales_top5_value"],
            {"measure": "outbound_value_myr", "group_by": [["product", "sku"]], "limit": 3},
            "limit(3)",
        ),
    ],
)
def test_typed_plan_the_certified_query_cannot_express_is_a_named_abstain(
    crew_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    q: str,
    plan: dict[str, Any],
    what: str,
) -> None:
    res, prompts = _ask(crew_client, monkeypatch, q, INVENTED, query_plan=plan)
    assert res.status_code == 200, res.text
    body = res.json()
    reason = f"{CANNOT}:{what}"
    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    assert body["refuse_reason"] == reason
    assert reason in body["answer"]
    assert "query_sql" not in body
    assert prompts == []


def test_declared_top_n_is_the_only_parameter_applied(
    crew_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cq_supplier_ranking declares LIMIT 10; a typed limit 3 is its first three rows."""
    plan = {"measure": "risk", "group_by": [["supplier", "supplier_id"]], "limit": 3}
    res, prompts = _ask(crew_client, monkeypatch, RANK_Q, INVENTED, query_plan=plan)
    assert res.status_code == 200, res.text
    sql = _assert_served(res.json(), "cq_supplier_ranking", prompts)
    oracle = _rows(cot_climb.gold_sql_for("cq_supplier_ranking"))
    assert _rows(sql) == oracle[:3]


def test_matching_plan_and_dms_default_limit_serve_unchanged(
    crew_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {"measure": "stock_value_myr", "group_by": [["product", "category"]], "limit": 50}
    res, prompts = _ask(
        crew_client, monkeypatch, MEASURES["cq_stock_value_by_category"], INVENTED, query_plan=plan
    )
    assert res.status_code == 200, res.text
    sql = _assert_served(res.json(), "cq_stock_value_by_category", prompts)
    assert Counter(_rows(sql)) == Counter(
        _rows(cot_climb.gold_sql_for("cq_stock_value_by_category"))
    )


def test_no_certified_measure_keeps_the_generate_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'how many skus' resolves to no certified measure: the model's SQL, as before."""
    res, prompts = _ask(
        client, monkeypatch, "how many skus", "SELECT COUNT(DISTINCT sku) * 1 AS n FROM inventory"
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    assert body["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert "count(distinct sku) * 1" in body["query_sql"].lower()
    assert _rows(body["query_sql"]) == [(7,)]
    assert body["generative"]["ok"] is True
    assert not str(body["generative"]["check"]).startswith("certified_query")
    assert any(p.startswith("generative_ask:") for p in prompts)


def test_certified_lookup_that_is_not_a_measure_keeps_the_generate_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cq_cold_storage is a plain lookup, not a measure: out of this ticket's scope."""
    res, prompts = _ask(
        client,
        monkeypatch,
        "Which locations are cold storage?",
        "SELECT location_code FROM locations WHERE location_code = 'A'",
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert body["generative"]["check"] != "certified_query:cq_cold_storage"
    assert any(p.startswith("generative_ask:") for p in prompts)


def test_generate_exception_is_a_named_200_abstain(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise KeyError("last_audit_date")

    monkeypatch.setattr(cot_climb, "climb", boom)
    res = client.post(
        "/v1/insights",
        json={"intent": "Which suppliers have an audit overdue?", "ask": True, "generate": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert body["refuse_reason"] == "generative_error:KeyError"
    assert _no_digits(body["answer"], "generative_error:KeyError")
    assert "query_sql" not in body
