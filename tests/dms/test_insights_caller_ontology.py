"""INSIGHTS-ONTO: ``POST /v1/insights`` uses the ontology the caller sends.

BIRD Mini-Dev (dms PR #312): n=500, ABSTAIN=500, 0 provider calls. 249 of
those died because ``InsightsAskIn`` silently dropped the ``ontology`` /
``mode`` DMS sent, ranked every question against the engine's supply-chain
pack, and refused before generation; 97 more matched demo-pack metrics.

These tests POST the exact body shape DMS ``compute_insights`` sends
(``cortex_client.compute._insights_body``) with a BIRD-like catalog
(``schools``, ``satscores``) and a stubbed FreeRoute ``complete``. They assert
the customer-visible envelope (status / badge / answer / values / plan_source /
query_sql / refuse_reason) and the rows the returned SQL produces on a lake
with those tables. Absent ontology (and a demo-spine ontology) keeps the old
pack ranking and the certified-measure serve. A malformed ontology is a named
422 ABSTAIN, never a 500 and never a model call.
"""

from __future__ import annotations

import json
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import certified_serve, insights
from packs.dms.security.rate_limit import reset_limiter

Q = "What is the highest average math SAT score among schools in Alameda county?"
GOOD_SQL = (
    "SELECT MAX(satscores.avgscrmath) AS top_math FROM satscores "
    "JOIN schools ON satscores.cds = schools.cdscode "
    "WHERE schools.county = 'Alameda'"
)
DEMO_TABLES = {"inventory", "suppliers", "locations", "shipments", "transactions", "alerts"}
RANK_Q = "Rank suppliers by combined risk and lead time score"


def bird_ontology(**over: Any) -> dict[str, Any]:
    """``retrieve_short_context`` shape for a SQL-source Space (BIRD california_schools)."""
    body: dict[str, Any] = {
        "verified": False,
        "methods": ["schema_sql", "summarize"],
        "schema": [
            {"table": "schools", "columns": ["cdscode", "county", "school"], "score": 4},
            {"table": "satscores", "columns": ["cds", "avgscrmath", "numtsttakr"], "score": 3},
        ],
        "encodings": {},
        "bound_values": {},
        "intent_slots": {},
        "measure_aliases": {},
        "measures": {
            "max_math_score": {"grain": "satscores", "description": "highest avgscrmath"}
        },
        "objects": {"schools": {"key": ["cdscode"]}, "satscores": {"key": ["cds"]}},
        "links": {
            "sat_school": {"from": "satscores", "to": "schools", "cardinality": "many_to_one"}
        },
        "columns": {},
    }
    body.update(over)
    return body


def dms_body(question: str, ontology: Any = None, **extra: Any) -> dict[str, Any]:
    """Exactly what ``cortex_client.compute._insights_body`` POSTs."""
    body: dict[str, Any] = {
        "intent": question,
        "question": question,
        "ask": False,
        "generate": True,
        "session_id": "bird",
        "space_id": "space-bird",
        "consumer": "dms",
        "mode": "ontology_plan",
        "model_preference": "free+normal",
    }
    if ontology is not None:
        body["ontology"] = ontology
        if isinstance(ontology, dict) and isinstance(ontology.get("intent_slots"), dict):
            if ontology["intent_slots"]:
                body["intent_slots"] = ontology["intent_slots"]
    body.update(extra)
    return body


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
        return {"ok": True, "text": "Use satscores joined to schools.", "identity": "t"}

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

    return TestClient(create_app(), client=("127.0.0.1", 5555))


def _post(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, Any],
    sql: str = GOOD_SQL,
) -> tuple[Any, list[str]]:
    from CortexOS.crew import freeroute as fr

    prompts: list[str] = []
    monkeypatch.setattr(fr, "complete", _stub(sql, prompts))
    return client.post("/v1/insights", json=body), prompts


def _bird_lake() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE schools (cdscode VARCHAR, county VARCHAR, school VARCHAR)")
    con.execute(
        "INSERT INTO schools VALUES ('1','Alameda','A'),('2','Alameda','B'),('3','Fresno','C')"
    )
    con.execute("CREATE TABLE satscores (cds VARCHAR, avgscrmath INTEGER, numtsttakr INTEGER)")
    con.execute("INSERT INTO satscores VALUES ('1',512,40),('2',640,55),('3',700,10)")
    return con


def test_bird_ontology_reaches_generation_and_sql_reads_only_caller_tables(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    res, prompts = _post(client, monkeypatch, dms_body(Q, bird_ontology()))
    assert res.status_code == 200, res.text
    body = res.json()
    # Reached generation (the BIRD run made 0 provider calls).
    gen_prompts = [p for p in prompts if p.startswith("generative_ask:")]
    assert gen_prompts, prompts
    assert "satscores" in gen_prompts[0] and "schools" in gen_prompts[0]
    for table in DEMO_TABLES:
        assert f"- {table}:" not in gen_prompts[0]
    # Customer envelope: validated SQL, no invented number, named plan.
    assert body["status"] == "ABSTAIN"
    assert body["phase"] == "generate"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert "Validated SQL" in body["answer"]
    assert body["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert body["ontology_source"] == "caller_ontology"
    assert body["ontology"]["source"] == "caller_ontology"
    assert body["generative"]["ok"] is True
    assert body["generative"]["valid"] is True
    sql = body["query_sql"]
    assert sql and body["sql_used"] == sql
    assert set(insights._tables_in_sql(sql)) <= {"schools", "satscores"}
    ranked = {row["where"]["table"] for row in body["ontology"]["locations"]}
    assert ranked == {"schools", "satscores"}
    assert body["ontology"]["certified"] == []
    # Nothing from DMS was dropped silently.
    received = body["api"]["received"]
    assert received["mode"] == "ontology_plan"
    assert received["model_preference"] == "free+normal"
    assert received["ontology"] is True
    # The returned SQL answers on the caller's lake: rows, not only SQL.
    rows = _bird_lake().execute(sql).fetchall()
    assert rows == [(640,)]


def test_sql_outside_caller_tables_is_refused_with_a_named_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    res, prompts = _post(
        client,
        monkeypatch,
        dms_body(Q, bird_ontology()),
        sql="SELECT COUNT(*) AS n FROM inventory",
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert any(p.startswith("generative_ask:") for p in prompts)
    assert body["status"] == "REFUSE"
    assert body["ontology_source"] == "caller_ontology"
    assert body["values"] == []
    assert "query_sql" not in body
    assert body["plan_source"] == "other"
    assert "sql reads tables outside ontology ranking: inventory" in body["answer"]


def test_non_demo_space_never_consults_the_engine_pack(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin for the 97 demo-metric matches: a caller Space never reaches packs/dms."""

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("engine pack consulted for a caller-ontology Space")

    monkeypatch.setattr(insights, "retrieve_ontology", boom)
    monkeypatch.setattr(certified_serve, "resolve", boom)
    # Wording that matches demo metrics ("count", "suppliers"-free but "sku count"-like).
    q = "How many schools in Alameda county have a count of SAT test takers above 50?"
    sql = (
        "SELECT COUNT(*) AS n FROM satscores JOIN schools ON satscores.cds = schools.cdscode "
        "WHERE schools.county = 'Alameda' AND satscores.numtsttakr > 50"
    )
    res, _prompts = _post(client, monkeypatch, dms_body(q, bird_ontology()), sql=sql)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ontology_source"] == "caller_ontology"
    assert body["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert body["values"] == []
    assert _bird_lake().execute(body["query_sql"]).fetchall() == [(1,)]


def test_caller_ontology_ask_never_replays_on_the_demo_bridge(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew.engine_bridge import LocalEngineBridge

    asked: list[str] = []

    async def fake_ask(self, question: str) -> dict[str, Any]:  # noqa: ARG001
        asked.append(question)
        return {"ok": True, "badge": "governed_metric", "rows": [{"n": 12}], "answer": "12"}

    monkeypatch.setattr(LocalEngineBridge, "ask", fake_ask)
    res, prompts = _post(
        client, monkeypatch, dms_body(Q, bird_ontology(), ask=True, generate=False)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert asked == []
    assert prompts == []
    assert body["status"] == "REFUSE"
    assert body["values"] == []
    assert "caller_ontology_ask_unsupported" in body["answer"]
    assert "12" not in body["answer"]


def _served(body: dict[str, Any]) -> tuple[Any, ...]:
    return (
        body["status"],
        body["badge"],
        body["plan_source"],
        body["generative"]["check"],
        body.get("query_sql"),
        body["values"],
    )


def test_absent_ontology_keeps_pack_ranking_and_certified_serve(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    res, prompts = _post(client, monkeypatch, dms_body(RANK_Q), sql="SELECT 1")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ontology_source"] == "engine_pack"
    assert body["generative"]["check"] == "certified_query:cq_supplier_ranking"
    assert not any(p.startswith("generative_ask:") for p in prompts)
    assert body["values"] == []
    assert {row["where"]["table"] for row in body["ontology"]["locations"]} <= DEMO_TABLES

    # A demo-spine catalog (DMS demo Space) is the same answer as no catalog.
    demo = bird_ontology(
        schema=[
            {"table": "suppliers", "columns": ["supplier_id", "risk_score"], "score": 5},
            {"table": "inventory", "columns": ["sku"], "score": 1},
        ],
        measures={},
        objects={},
        links={},
    )
    res2, prompts2 = _post(client, monkeypatch, dms_body(RANK_Q, demo), sql="SELECT 1")
    assert res2.status_code == 200, res2.text
    body2 = res2.json()
    assert body2["ontology_source"] == "engine_pack"
    assert _served(body2) == _served(body)
    assert not any(p.startswith("generative_ask:") for p in prompts2)


_TOO_BIG = bird_ontology(encodings={"x": "y" * (70 * 1024)})


@pytest.mark.parametrize(
    ("ontology", "code"),
    [
        ("schools,satscores", "bad_shape"),
        (bird_ontology(schema="schools"), "bad_shape"),
        (
            bird_ontology(schema=[{"table": "schools; DROP TABLE x", "columns": []}]),
            "bad_identifier",
        ),
        (
            bird_ontology(schema=[{"table": "schools", "columns": ["county) OR (1=1"]}]),
            "bad_identifier",
        ),
        (
            bird_ontology(measures={"m": {"grain": "schools", "sql": "SELECT 1"}}),
            "sql_in_ontology",
        ),
        (
            bird_ontology(
                measures={"m": {"grain": "schools", "description": "select * from secrets"}}
            ),
            "sql_in_ontology",
        ),
        (_TOO_BIG, "too_large"),
    ],
)
def test_malformed_ontology_is_a_named_422_abstain_and_calls_no_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, ontology: Any, code: str
) -> None:
    res, prompts = _post(client, monkeypatch, dms_body(Q, ontology))
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert body["refuse_reason"] == f"caller_ontology_invalid:{code}"
    assert body["values"] == []
    assert body["sql_used"] is None
    assert f"caller_ontology_invalid:{code}" in body["answer"]
    assert prompts == []
    assert "Traceback" not in json.dumps(body)


def test_bad_query_plan_and_unknown_fields_are_named_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    res, prompts = _post(
        client,
        monkeypatch,
        dms_body(Q, bird_ontology(), query_plan={"measure": "x; drop table y"}),
    )
    assert res.status_code == 422, res.text
    assert res.json()["refuse_reason"] == "caller_ontology_invalid:bad_identifier"
    assert prompts == []

    res2, prompts2 = _post(client, monkeypatch, dms_body(Q, bird_ontology(), ontolgy={}))
    assert res2.status_code == 422, res2.text
    body2 = res2.json()
    assert body2["status"] == "ABSTAIN"
    assert body2["refuse_reason"] == "unknown_request_fields"
    assert "ontolgy" in body2["detail"]
    assert body2["values"] == []
    assert prompts2 == []


def test_ranked_retry_body_is_accepted_and_plan_reaches_the_prompt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DMS's one ranked-slot retry adds query_plan / ranked_metric / generate_retry."""
    body = dms_body(
        Q,
        bird_ontology(),
        query_plan={"measure": "max_math_score", "group_by": [], "filters": []},
        ranked_metric="max_math_score",
        generate_retry="ranked_slots",
    )
    res, prompts = _post(client, monkeypatch, body)
    assert res.status_code == 200, res.text
    out = res.json()
    gen_prompts = [p for p in prompts if p.startswith("generative_ask:")]
    assert gen_prompts and "ONTOLOGY PLAN measure=max_math_score" in gen_prompts[0]
    assert out["api"]["received"]["generate_retry"] == "ranked_slots"
    assert out["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert _bird_lake().execute(out["query_sql"]).fetchall() == [(640,)]
