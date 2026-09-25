"""GEN-CERTIFIED-MEASURE-01: a certified formula that resolves the ask is law.

dms#231 F3: on cq_supplier_ranking the model wrote its own risk formula and
the static gate validated it, so DMS executed a plausible ranking built on an
invented weight. Generate must compute the certified expression or abstain
with ``certified_measure_not_used:<id>`` -- a 200, never a figure, never a 5xx.
Assertions are on the HTTP envelope (status/badge/answer/values) and on the
rows the returned SQL produces; SQL checks are only in addition.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import cot_climb, insights
from packs.dms.security.rate_limit import reset_limiter

RANK_Q = "Rank suppliers by combined risk and lead time score"
MISS = "certified_measure_not_used:cq_supplier_ranking"
INVENTED = (
    "SELECT supplier_id, ROUND(risk_score * 0.5 + lead_time_days * 0.5, 3) AS score "
    "FROM suppliers ORDER BY score DESC LIMIT 10"
)
# Certified expression, reordered and table-qualified: still the certified measure.
CERTIFIED_REWRITE = (
    "SELECT s.supplier_id, "
    "ROUND(s.lead_time_days / 60.0 * 0.35 + s.risk_score * 0.65, 3) AS ranking_score "
    "FROM suppliers s "
    "ORDER BY ranking_score DESC, risk_score DESC, lead_time_days DESC LIMIT 10"
)


def _stub(sql: str, prompts: list[str]) -> Any:
    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        if purpose == "generative_ask":
            prompts.append(prompt or "")
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


def _ask(client: TestClient, monkeypatch: pytest.MonkeyPatch, q: str, sql: str) -> tuple[Any, list[str]]:
    from CortexOS.crew import freeroute as fr

    prompts: list[str] = []
    monkeypatch.setattr(fr, "complete", _stub(sql, prompts))
    res = client.post("/v1/insights", json={"intent": q, "ask": False, "generate": True})
    return res, prompts


def _suppliers_rows(sql: str) -> list[tuple[Any, ...]]:
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        con.execute(
            "CREATE TABLE suppliers (supplier_id VARCHAR, risk_score DOUBLE, lead_time_days INTEGER)"
        )
        con.execute(
            "INSERT INTO suppliers VALUES ('S1', 0.9, 10), ('S2', 0.2, 55), "
            "('S3', 0.5, 30), ('S4', 0.7, 5), ('S5', 0.1, 60)"
        )
        return [tuple(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def test_invented_formula_is_a_named_200_abstain_not_an_answer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    res, prompts = _ask(client, monkeypatch, RANK_Q, INVENTED)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert body["refuse_reason"] == MISS
    assert MISS in body["answer"]
    assert not re.search(r"\d", body["answer"].replace(MISS, "")), body["answer"]
    # Nothing for DMS to execute: no query_sql, not labelled ontology_plan.
    assert "query_sql" not in body
    assert body["plan_source"] == insights.PLAN_SOURCE_OTHER
    assert body["generative"]["ok"] is False
    assert body["generative"]["refuse_reason"] == MISS
    assert any(u.get("why") == MISS for u in body["validation"]["unsure"])
    text = insights.render_tool_text(body)
    assert "status: ABSTAIN" in text
    # The model was shown the certified expression and retried within the horizon.
    assert prompts and all("CERTIFIED MEASURE cq_supplier_ranking" in p for p in prompts)
    assert len(prompts) == cot_climb.HORIZON


def test_certified_expression_passes_and_rows_equal_the_oracle(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    res, _ = _ask(client, monkeypatch, RANK_Q, CERTIFIED_REWRITE)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"  # validated, not executed in crew
    assert body["values"] == []
    assert body["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    sql = body["query_sql"]
    oracle = cot_climb.gold_sql_for("cq_supplier_ranking")
    got_rows = _suppliers_rows(sql)
    want_rows = _suppliers_rows(oracle)
    assert got_rows and Counter(got_rows) == Counter(want_rows)


def test_invented_formula_rows_are_wrong_so_the_gate_is_load_bearing() -> None:
    """The case the grain guard cannot catch: right grain, wrong values."""
    oracle = cot_climb.gold_sql_for("cq_supplier_ranking")
    assert Counter(_suppliers_rows(INVENTED)) != Counter(_suppliers_rows(oracle))


def test_no_certified_measure_behaves_as_before(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No certified formula resolves 'how many skus': prompt and envelope unchanged.

    Passes on main@27f79ea too; that is the point (behaviour pin, not a new rule).
    """
    res, prompts = _ask(
        client, monkeypatch, "how many skus", "SELECT COUNT(DISTINCT sku) * 1 AS n FROM inventory"
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    assert body["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert "count(distinct sku) * 1" in body["query_sql"].lower()
    assert body["generative"]["ok"] is True
    assert len(prompts) == 1
    assert "CERTIFIED MEASURE" not in prompts[0]


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
    assert "query_sql" not in body


CERT = "ROUND((risk_score * 0.65) + ((lead_time_days / 60.0) * 0.35), 3)"
HALF = "ROUND(risk_score * 0.5 + lead_time_days * 0.5, 3)"
# Verifier probes P1-P4 and P10: the certified expression is somewhere in the
# tree, but the answer is ranked by an invented formula (or reversed).
BYPASS = {
    "where_only": (
        f"SELECT supplier_id, {HALF} AS ranking_score FROM suppliers "
        f"WHERE {CERT} IS NOT NULL ORDER BY ranking_score DESC LIMIT 10"
    ),
    "projected_but_ordered_by_invented": (
        f"SELECT supplier_id, {CERT} AS ranking_score FROM suppliers "
        "ORDER BY risk_score * 0.5 + lead_time_days * 0.5 DESC LIMIT 3"
    ),
    "dead_case_branch": (
        f"SELECT supplier_id, CASE WHEN 1 = 0 THEN {CERT} ELSE {HALF} END AS ranking_score "
        "FROM suppliers ORDER BY ranking_score DESC LIMIT 10"
    ),
    "unused_subquery_column": (
        f"SELECT supplier_id, {HALF} AS ranking_score FROM "
        f"(SELECT *, {CERT} AS x FROM suppliers) t ORDER BY ranking_score DESC LIMIT 10"
    ),
    "extra_invented_column": (
        f"SELECT supplier_id, {CERT} AS ranking_score, {HALF} AS alt FROM suppliers "
        "ORDER BY ranking_score DESC LIMIT 10"
    ),
    "reversed_ranking": (
        f"SELECT supplier_id, {CERT} AS ranking_score FROM suppliers "
        "ORDER BY ranking_score ASC LIMIT 10"
    ),
    "not_ranked_by_measure": (
        f"SELECT supplier_id, {CERT} AS ranking_score FROM suppliers "
        "ORDER BY supplier_id LIMIT 10"
    ),
}


@pytest.mark.parametrize("name", sorted(BYPASS))
def test_certified_expression_hidden_elsewhere_is_still_a_named_abstain(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    sql = BYPASS[name]
    oracle = cot_climb.gold_sql_for("cq_supplier_ranking")
    # The bypass is load-bearing: its ranked rows are not the oracle's.
    assert _suppliers_rows(sql) != _suppliers_rows(oracle)
    res, _ = _ask(client, monkeypatch, RANK_Q, sql)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert body["refuse_reason"] == MISS
    assert "query_sql" not in body
    assert body["plan_source"] == insights.PLAN_SOURCE_OTHER
    assert not re.search(r"\d", body["answer"].replace(MISS, "")), body["answer"]


# Same measure, different spelling: numeric literal, regrouping, ordinal ORDER
# BY, outer ROUND dropped, alias through a CTE. None of these may be refused.
EQUIVALENT = {
    "cq_supplier_ranking": [
        "SELECT supplier_id, ROUND(risk_score * 0.65 + lead_time_days / 60 * 0.35, 3) AS s "
        "FROM suppliers ORDER BY s DESC LIMIT 10",
        "SELECT supplier_id, ROUND(0.65 * risk_score + 0.35 * lead_time_days / 60.0, 3) AS s "
        "FROM suppliers ORDER BY 2 DESC, risk_score DESC LIMIT 10",
        "WITH r AS (SELECT supplier_id, risk_score * 0.65 + lead_time_days * 0.35 / 60 AS s "
        "FROM suppliers) SELECT supplier_id, s FROM r ORDER BY s DESC LIMIT 10",
    ],
    "cq_capacity_utilisation": [
        "SELECT location_code, ROUND(current_load_kg / capacity_kg * 100, 1) AS pct FROM locations",
        "SELECT location_code, current_load_kg * 100.0 / capacity_kg AS pct FROM locations "
        "ORDER BY pct DESC",
    ],
    "cq_sales_top5_value": [
        "SELECT sku, SUM(quantity_kg * unit_cost_myr) AS v FROM transactions "
        "WHERE txn_type = 'OUT' GROUP BY sku ORDER BY v DESC, sku ASC LIMIT 5",
        "SELECT sku, ROUND(SUM(unit_cost_myr * quantity_kg), 2) FROM transactions "
        "WHERE txn_type = 'OUT' GROUP BY sku ORDER BY 2 DESC LIMIT 5",
    ],
    "cq_stock_value_by_category": [
        "SELECT category, SUM(unit_cost_myr * quantity_kg) AS v FROM inventory GROUP BY category",
        "SELECT category, ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS v FROM inventory "
        "GROUP BY category ORDER BY v DESC",
    ],
    "cq_spend_by_country": [
        "SELECT s.country, SUM(i.unit_cost_myr * i.quantity_kg) AS spend FROM inventory i "
        "JOIN suppliers s ON i.supplier_id = s.supplier_id GROUP BY s.country ORDER BY spend DESC",
    ],
    "cq_top3_category_sales": [
        "WITH c AS (SELECT DISTINCT sku, category FROM inventory) "
        "SELECT c.category, SUM(t.unit_cost_myr * t.quantity_kg) AS v FROM transactions t "
        "JOIN c ON t.sku = c.sku WHERE t.txn_type = 'OUT' GROUP BY c.category "
        "ORDER BY v DESC LIMIT 3",
    ],
}
# A different quantity over the same columns: must miss.
DIFFERENT = {
    "cq_supplier_ranking": [
        "SELECT supplier_id, ROUND(risk_score * 0.35 + lead_time_days / 60.0 * 0.65, 3) AS s "
        "FROM suppliers ORDER BY s DESC LIMIT 10",
    ],
    "cq_capacity_utilisation": [
        "SELECT location_code, ROUND(capacity_kg / current_load_kg * 100, 1) AS pct FROM locations",
    ],
    "cq_sales_top5_value": [
        "SELECT sku, SUM(quantity_kg) * SUM(unit_cost_myr) AS v FROM transactions "
        "WHERE txn_type = 'OUT' GROUP BY sku ORDER BY v DESC LIMIT 5",
    ],
    "cq_stock_value_by_category": [
        "SELECT category, AVG(quantity_kg * unit_cost_myr) AS v FROM inventory GROUP BY category",
    ],
}


def _measure_for(mid: str) -> dict[str, Any]:
    ranking = {"certified": [{"id": mid, "importance": {"score": 25}}]}
    measure = cot_climb.certified_measure(ranking)
    assert measure and measure["id"] == mid
    return measure


def test_every_bound_certified_gold_passes_its_own_gate() -> None:
    """The 9 affected curated asks: the certified SQL itself is never refused."""
    bound = []
    for _cid, intent in cot_climb.DMS_180_CURATED:
        measure = cot_climb.certified_measure(insights.retrieve_ontology(intent))
        if measure:
            bound.append(measure["id"])
            gold = cot_climb.gold_sql_for(measure["id"])
            assert cot_climb.certified_measure_miss(gold, measure) == "", measure["id"]
    assert "cq_supplier_ranking" in bound


@pytest.mark.parametrize(
    ("mid", "sql"), [(m, s) for m, rows in EQUIVALENT.items() for s in rows]
)
def test_equivalent_spelling_is_not_refused(mid: str, sql: str) -> None:
    assert cot_climb.certified_measure_miss(sql, _measure_for(mid)) == ""


@pytest.mark.parametrize(
    ("mid", "sql"), [(m, s) for m, rows in DIFFERENT.items() for s in rows]
)
def test_different_quantity_is_refused(mid: str, sql: str) -> None:
    assert cot_climb.certified_measure_miss(sql, _measure_for(mid)) == (
        f"{cot_climb.CERTIFIED_MEASURE_MISS}:{mid}"
    )
