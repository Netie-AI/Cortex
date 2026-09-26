"""INSIGHTS-ONTO: ``POST /v1/insights`` uses the ontology the caller sends.

BIRD Mini-Dev (dms PR #312): n=500, ABSTAIN=500, 0 provider calls. 249 of
those died because ``InsightsAskIn`` silently dropped the ``ontology`` /
``mode`` DMS sent, ranked every question against the engine's supply-chain
pack, and refused before generation; 97 more matched demo-pack metrics.

Shared naming rule (both repos): DMS says which Space this is with
``ontology.source``. ``"space"``: the caller catalog is the whole universe;
tables cross as ``schema.table`` (or bare), generated SQL may reference only a
declared name (a bare name only when it resolves to exactly one declared
table), and the pack / its certified formulas are never consulted.
``"demo"`` or absent: the engine pack ranks exactly as it did before
INSIGHTS-ONTO, whatever the ontology body says (its truncated column lists are
never a column allowlist).

These tests POST the exact body shape DMS ``compute_insights`` sends
(``cortex_client.compute._insights_body``) with a stubbed FreeRoute
``complete``. They assert the customer-visible envelope (status / badge /
answer / values / plan_source / query_sql / refuse_reason) and the rows the
returned SQL produces on a lake with those tables. A malformed space ontology
is a named 4xx ABSTAIN, never a 500 and never a model call.
"""

from __future__ import annotations

import json
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import certified_serve, cot_climb, insights
from CortexOS.insights.routes import InsightsAskIn
from packs.dms.security.rate_limit import reset_limiter

Q = "What is the highest average math SAT score among schools in Alameda county?"
GOOD_SQL = (
    "SELECT MAX(t.avgscrmath) AS top_math FROM bronze.satscores AS t "
    "JOIN bronze.schools AS s ON t.cds = s.cdscode "
    "WHERE s.county = 'Alameda'"
)
BARE_SQL = (
    "SELECT MAX(satscores.avgscrmath) AS top_math FROM satscores "
    "JOIN schools ON satscores.cds = schools.cdscode "
    "WHERE schools.county = 'Alameda'"
)
DEMO_TABLES = {"inventory", "suppliers", "locations", "shipments", "transactions", "alerts"}
RANK_Q = "Rank suppliers by combined risk and lead time score"


def space_ontology(**over: Any) -> dict[str, Any]:
    """``retrieve_short_context`` shape for a SQL-source Space (BIRD california_schools)."""
    body: dict[str, Any] = {
        "source": "space",
        "verified": False,
        "methods": ["schema_sql", "summarize"],
        "schema": [
            {"table": "bronze.schools", "columns": ["cdscode", "county", "school"], "score": 4},
            {
                "table": "bronze.satscores",
                "columns": ["cds", "avgscrmath", "numtsttakr"],
                "score": 3,
            },
        ],
        "encodings": {},
        "bound_values": {},
        "intent_slots": {},
        "measure_aliases": {},
        "measures": {
            "max_math_score": {"grain": "bronze.satscores", "description": "highest avgscrmath"}
        },
        "objects": {"bronze.schools": {"key": ["cdscode"]}, "bronze.satscores": {"key": ["cds"]}},
        "links": {
            "sat_school": {
                "from": "bronze.satscores",
                "to": "bronze.schools",
                "cardinality": "many_to_one",
            }
        },
        "columns": {},
    }
    body.update(over)
    return body


def demo_ontology(**over: Any) -> dict[str, Any]:
    """What DMS sends for the demo Space: pack tables, *truncated* column lists."""
    body: dict[str, Any] = {
        "verified": True,
        "methods": ["hybrid_fuse", "ontology", "schema_sql", "summarize"],
        "schema": [
            {"table": "suppliers", "columns": ["supplier_id", "risk_score"], "score": 5},
            {"table": "inventory", "columns": ["sku"], "score": 1},
        ],
        "encodings": {"inventory.category": ["CHEM", "FOOD"]},
        "bound_values": {},
        "intent_slots": {"measure": "supplier_rank"},
        "measure_aliases": {"rank": "supplier_rank"},
        "measures": {"supplier_rank": {"grain": "supplier", "description": "risk; lead time"}},
        "objects": {"supplier": {"key": ["supplier_id"]}},
        "links": {},
        "columns": {"supplier": ["supplier_id", "country", "risk_score"]},
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
    """``bronze`` holds the Space's tables; ``main`` holds decoys of the same name."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA bronze")
    con.execute("CREATE TABLE bronze.schools (cdscode VARCHAR, county VARCHAR, school VARCHAR)")
    con.execute(
        "INSERT INTO bronze.schools VALUES "
        "('1','Alameda','A'),('2','Alameda','B'),('3','Fresno','C')"
    )
    con.execute(
        "CREATE TABLE bronze.satscores (cds VARCHAR, avgscrmath INTEGER, numtsttakr INTEGER)"
    )
    con.execute("INSERT INTO bronze.satscores VALUES ('1',512,40),('2',640,55),('3',700,10)")
    con.execute("CREATE TABLE main.satscores (cds VARCHAR, avgscrmath INTEGER, numtsttakr INTEGER)")
    con.execute("INSERT INTO main.satscores VALUES ('1',999,1)")
    con.execute("CREATE TABLE main.schools (cdscode VARCHAR, county VARCHAR, school VARCHAR)")
    con.execute("INSERT INTO main.schools VALUES ('1','Alameda','decoy')")
    return con


def _gen_prompts(prompts: list[str]) -> list[str]:
    return [p for p in prompts if p.startswith("generative_ask:")]


def _assert_refused(body: dict[str, Any], needle: str) -> None:
    assert body["status"] == "REFUSE"
    assert body["badge"] != "certified"
    assert body["values"] == []
    assert "query_sql" not in body
    assert body["plan_source"] == "other"
    assert needle in body["answer"], body["answer"]


# -- (a) source=space, qualified catalog ------------------------------------


def test_qualified_catalog_reaches_generation_and_qualified_sql_is_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    res, prompts = _post(client, monkeypatch, dms_body(Q, space_ontology()))
    assert res.status_code == 200, res.text
    body = res.json()
    gen = _gen_prompts(prompts)
    assert gen, prompts  # the BIRD run made 0 provider calls
    assert "bronze.satscores" in gen[0] and "bronze.schools" in gen[0]
    for table in DEMO_TABLES:
        assert f"- {table}:" not in gen[0]
    # Customer envelope: validated SQL, no invented number, named plan.
    assert body["status"] == "ABSTAIN"
    assert body["phase"] == "generate"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert "Validated SQL" in body["answer"]
    assert body["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert body["ontology_source"] == "caller_ontology"
    assert body["generative"]["valid"] is True
    assert body["generative"]["check"].startswith("caller catalog")
    sql = body["query_sql"]
    assert sql == GOOD_SQL and body["sql_used"] == sql
    ranked = {row["where"]["table"] for row in body["ontology"]["locations"]}
    assert ranked == {"bronze.schools", "bronze.satscores"}
    assert body["ontology"]["certified"] == []
    received = body["api"]["received"]
    assert received["mode"] == "ontology_plan"
    assert received["wire_ontology_source"] == "space"
    # Rows on the caller's lake, from bronze, not the main.* decoys.
    assert _bird_lake().execute(sql).fetchall() == [(640,)]


def test_bare_reference_resolves_to_the_one_declared_qualified_table(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    res, _prompts = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=BARE_SQL)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    sql = body["query_sql"]
    assert "bronze.satscores" in sql and "bronze.schools" in sql
    # Bare SQL on this lake would read the main.* decoy (999); resolved SQL reads bronze.
    assert _bird_lake().execute(BARE_SQL).fetchall() == [(999,)]
    assert _bird_lake().execute(sql).fetchall() == [(640,)]


@pytest.mark.parametrize(
    ("sql", "needle"),
    [
        ("SELECT COUNT(*) AS n FROM inventory", "table inventory is not in the caller catalog"),
        ("SELECT COUNT(*) AS n FROM bronze.frpm", "table bronze.frpm is not in the caller catalog"),
        ("SELECT COUNT(*) AS n FROM silver.schools", "table silver.schools is not in the caller"),
        ("SELECT COUNT(*) AS n FROM main.schools", "table main.schools is not in the caller"),
        ("SELECT COUNT(*) FROM memory.bronze.schools", "cross-catalog table reference refused"),
        ("SELECT * FROM read_csv('/etc/passwd')", "refused"),
        ("SELECT s.phone FROM bronze.schools AS s", "column bronze.schools.phone is not declared"),
    ],
)
def test_sql_outside_the_declared_catalog_is_a_named_refuse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, sql: str, needle: str
) -> None:
    res, prompts = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=sql)
    assert res.status_code == 200, res.text
    body = res.json()
    assert _gen_prompts(prompts)  # generation was reached; the check refused it
    assert body["ontology_source"] == "caller_ontology"
    _assert_refused(body, needle)


def test_ambiguous_bare_reference_is_a_named_refuse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    onto = space_ontology(
        schema=[
            {"table": "bronze.schools", "columns": [], "score": 2},
            {"table": "silver.schools", "columns": [], "score": 1},
        ],
        measures={},
        objects={},
        links={},
    )
    res, _ = _post(client, monkeypatch, dms_body(Q, onto), sql="SELECT COUNT(*) FROM schools")
    assert res.status_code == 200, res.text
    _assert_refused(res.json(), "bare table schools is ambiguous")


def test_table_with_no_declared_columns_is_checked_at_table_level_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No column list for a table means no guessed allowlist for it."""
    onto = space_ontology(
        schema=[{"table": "bronze.schools", "columns": [], "score": 2}],
        measures={},
        objects={},
        links={},
    )
    sql = "SELECT COUNT(*) AS n FROM bronze.schools WHERE county = 'Alameda'"
    res, _ = _post(client, monkeypatch, dms_body(Q, onto), sql=sql)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    assert body["generative"]["check"].startswith("table-level check only")
    assert _bird_lake().execute(body["query_sql"]).fetchall() == [(2,)]


def test_bare_catalog_names_still_work_for_space(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    onto = space_ontology(
        schema=[
            {"table": "schools", "columns": ["cdscode", "county"], "score": 2},
            {"table": "satscores", "columns": ["cds", "avgscrmath"], "score": 2},
        ],
        measures={},
        objects={},
        links={"sat_school": {"from": "satscores", "to": "schools"}},
    )
    res, _ = _post(client, monkeypatch, dms_body(Q, onto), sql=BARE_SQL)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert body["query_sql"] == BARE_SQL
    assert _bird_lake().execute(body["query_sql"]).fetchall() == [(999,)]


def test_space_never_consults_the_engine_pack(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin for the 97 demo-metric matches: a caller Space never reaches packs/dms."""

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("engine pack consulted for a source=space Space")

    monkeypatch.setattr(insights, "retrieve_ontology", boom)
    monkeypatch.setattr(certified_serve, "resolve", boom)
    # Demo wording and a pack table name, on a customer Space.
    onto = space_ontology(
        schema=[{"table": "crm.suppliers", "columns": ["vendor_name", "score"], "score": 5}],
        measures={},
        objects={},
        links={},
    )
    sql = "SELECT vendor_name, score FROM suppliers ORDER BY score DESC"
    res, prompts = _post(client, monkeypatch, dms_body(RANK_Q, onto), sql=sql)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ontology_source"] == "caller_ontology"
    assert body["ontology"]["certified"] == []
    assert not str(body["generative"].get("check") or "").startswith("certified_query")
    assert _gen_prompts(prompts)
    assert body["values"] == []
    out = body["query_sql"]
    assert "crm.suppliers" in out and "risk_score" not in out
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA crm")
    con.execute("CREATE TABLE crm.suppliers (vendor_name VARCHAR, score DOUBLE)")
    con.execute("INSERT INTO crm.suppliers VALUES ('Acme', 0.9), ('Beta', 0.4)")
    assert con.execute(out).fetchall() == [("Acme", 0.9), ("Beta", 0.4)]


def test_space_ask_never_replays_on_the_demo_bridge(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew.engine_bridge import LocalEngineBridge

    asked: list[str] = []

    async def fake_ask(self, question: str) -> dict[str, Any]:  # noqa: ARG001
        asked.append(question)
        return {"ok": True, "badge": "governed_metric", "rows": [{"n": 12}], "answer": "12"}

    monkeypatch.setattr(LocalEngineBridge, "ask", fake_ask)
    res, prompts = _post(
        client, monkeypatch, dms_body(Q, space_ontology(), ask=True, generate=False)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert asked == []
    assert prompts == []
    assert body["status"] == "REFUSE"
    assert body["values"] == []
    assert "caller_ontology_ask_unsupported" in body["answer"]
    assert "12" not in body["answer"]


def test_ranked_retry_body_is_accepted_and_plan_reaches_the_prompt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DMS's one ranked-slot retry adds query_plan / ranked_metric / generate_retry."""
    body = dms_body(
        Q,
        space_ontology(),
        query_plan={"measure": "max_math_score", "group_by": [], "filters": []},
        ranked_metric="max_math_score",
        generate_retry="ranked_slots",
    )
    res, prompts = _post(client, monkeypatch, body)
    assert res.status_code == 200, res.text
    out = res.json()
    gen = _gen_prompts(prompts)
    assert gen and "ONTOLOGY PLAN measure=max_math_score" in gen[0]
    assert "metric max_math_score tables=bronze.satscores" in gen[0]
    assert out["api"]["received"]["generate_retry"] == "ranked_slots"
    assert out["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert _bird_lake().execute(out["query_sql"]).fetchall() == [(640,)]


@pytest.mark.parametrize(
    "question",
    ["What is the total number of SKUs with quantity below reorder level?", RANK_Q],
)
def test_space_ontology_with_no_tables_abstains_by_name_and_never_ranks_the_pack(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    """The original BIRD failure. An empty space schema is never the demo pack."""

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("engine pack consulted for an empty caller ontology")

    monkeypatch.setattr(insights, "retrieve_ontology", boom)
    monkeypatch.setattr(certified_serve, "resolve", boom)
    onto = space_ontology(schema=[], measures={}, objects={}, links={})
    res, prompts = _post(client, monkeypatch, dms_body(question, onto))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["refuse_reason"] == "caller_ontology_empty"
    assert "caller_ontology_empty" in body["answer"]
    assert body["values"] == []
    assert body["sql_used"] is None
    assert "query_sql" not in body
    assert prompts == []


# -- (b) source=demo or absent: exactly the pre-INSIGHTS-ONTO behaviour --------


def _lake_demo() -> duckdb.DuckDBPyConnection:
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
        "('K3','CHEM','S3','L2',7,3)"
    )
    return con


def _served(body: dict[str, Any]) -> tuple[Any, ...]:
    return (
        body["status"],
        body["badge"],
        body["plan_source"],
        body["answer"],
        body["generative"].get("check"),
        body["generative"].get("valid"),
        body.get("query_sql"),
        body["values"],
        body["ontology_source"],
        json.dumps(body["ontology"], sort_keys=True),
    )


DEMO_BODIES = [
    pytest.param(None, id="no-ontology"),
    pytest.param(demo_ontology(), id="source-absent"),
    pytest.param(demo_ontology(source="demo"), id="source-demo"),
    pytest.param(demo_ontology(source=None), id="source-null"),
]


@pytest.mark.parametrize("ontology", DEMO_BODIES)
def test_demo_certified_question_is_served_end_to_end_unchanged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, ontology: Any
) -> None:
    """Pin: RANK_Q is served as the stored certified query, rows equal the oracle."""
    res, prompts = _post(client, monkeypatch, dms_body(RANK_Q, ontology), sql="SELECT 1")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ontology_source"] == "engine_pack"
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert body["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert "Served certified query cq_supplier_ranking" in body["answer"]
    assert body["generative"]["check"] == "certified_query:cq_supplier_ranking"
    assert not _gen_prompts(prompts)  # the model is never asked for a certified measure
    oracle = cot_climb.gold_sql_for("cq_supplier_ranking")
    assert body["query_sql"] == oracle
    rows = _lake_demo().execute(body["query_sql"]).fetchall()
    assert rows and rows == _lake_demo().execute(oracle).fetchall()
    assert {row["where"]["table"] for row in body["ontology"]["locations"]} <= DEMO_TABLES


DEMO_GEN_Q = "Which SKUs in the CHEM category have the highest unit cost?"
#: Uses pack columns the demo body's truncated lists do not name (category,
#: unit_cost_myr): it validated before INSIGHTS-ONTO and must still validate.
DEMO_GEN_SQL = (
    "SELECT sku, unit_cost_myr FROM inventory WHERE category = 'CHEM' "
    "ORDER BY unit_cost_myr DESC LIMIT 5"
)


def test_demo_generated_sql_validates_identically_with_or_without_the_demo_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = []
    for param in DEMO_BODIES:
        ontology = param.values[0]
        res, prompts = _post(
            client, monkeypatch, dms_body(DEMO_GEN_Q, ontology), sql=DEMO_GEN_SQL
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert _gen_prompts(prompts)
        got.append((_served(body), _gen_prompts(prompts)))
    baseline_served, baseline_prompts = got[0]
    for served, gen in got[1:]:
        assert served == baseline_served
        assert gen == baseline_prompts
    status, badge, _plan, answer, _check, valid, sql, values, source, _onto = baseline_served
    assert (status, badge, valid, values, source) == ("ABSTAIN", "abstain", True, [], "engine_pack")
    assert "Validated SQL" in answer
    assert sql
    assert _lake_demo().execute(sql).fetchall() == [("K3", 3.0), ("K1", 2.5)]


def test_demo_body_is_not_validated_as_a_catalog(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A demo body carrying a name the space validator refuses is still the demo."""
    onto = demo_ontology(schema=[{"table": "schools\n", "columns": ["a b"], "score": 1}])
    res, _ = _post(client, monkeypatch, dms_body(RANK_Q, onto), sql="SELECT 1")
    assert res.status_code == 200, res.text
    assert res.json()["generative"]["check"] == "certified_query:cq_supplier_ranking"


# -- (c) malformed space ontology: named 4xx, no model call -------------------


_TOO_BIG = space_ontology(encodings={"x": "y" * (70 * 1024)})


def _schema(table: Any, columns: Any = None) -> dict[str, Any]:
    return space_ontology(
        schema=[{"table": table, "columns": columns or [], "score": 1}],
        measures={},
        objects={},
        links={},
    )


@pytest.mark.parametrize(
    ("ontology", "code"),
    [
        ("schools,satscores", "bad_shape"),
        (space_ontology(schema="schools"), "bad_shape"),
        (_schema("schools\n"), "bad_identifier"),
        (_schema("bronze.schools\n"), "bad_identifier"),
        (_schema('"schools"'), "bad_identifier"),
        (_schema('bronze."schools"'), "bad_identifier"),
        (_schema("a.b.c"), "bad_identifier"),
        (_schema("bronze..schools"), "bad_identifier"),
        (_schema(".schools"), "bad_identifier"),
        (_schema("bronze.sch ools"), "bad_identifier"),
        (_schema("schools; DROP TABLE x"), "bad_identifier"),
        (_schema("bronze.schools", ["county\n"]), "bad_identifier"),
        (_schema("bronze.schools", ["s.county"]), "bad_identifier"),
        (_schema("bronze.schools", ["county) OR (1=1"]), "bad_identifier"),
        (
            space_ontology(measures={"m": {"grain": "bronze.schools", "sql": "SELECT 1"}}),
            "sql_in_ontology",
        ),
        (
            space_ontology(
                measures={"m": {"grain": "bronze.schools", "description": "select * from x"}}
            ),
            "sql_in_ontology",
        ),
        (space_ontology(source="warehouse"), "bad_source"),
        (demo_ontology(source="Space"), "bad_source"),
        (_TOO_BIG, "too_large"),
    ],
)
def test_malformed_space_ontology_is_a_named_422_abstain_and_calls_no_model(
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
        dms_body(Q, space_ontology(), query_plan={"measure": "x; drop table y"}),
    )
    assert res.status_code == 422, res.text
    assert res.json()["refuse_reason"] == "caller_ontology_invalid:bad_identifier"
    assert prompts == []

    res2, prompts2 = _post(client, monkeypatch, dms_body(Q, space_ontology(), ontolgy={}))
    assert res2.status_code == 422, res2.text
    body2 = res2.json()
    assert body2["status"] == "ABSTAIN"
    assert body2["refuse_reason"] == "unknown_request_fields"
    assert "ontolgy" in body2["detail"]
    assert body2["values"] == []
    assert prompts2 == []


@pytest.mark.parametrize("raw_score", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_non_finite_schema_score_is_a_named_422_not_a_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, raw_score: str
) -> None:
    from CortexOS.crew import freeroute as fr

    prompts: list[str] = []
    monkeypatch.setattr(fr, "complete", _stub(GOOD_SQL, prompts))
    onto = json.dumps(space_ontology()).replace('"score": 4', f'"score": {raw_score}')
    assert raw_score in onto
    body = json.dumps(dms_body(Q)).rstrip("}") + ', "ontology": ' + onto + "}"
    safe = TestClient(client.app, client=("127.0.0.1", 5555), raise_server_exceptions=False)
    res = safe.post("/v1/insights", content=body, headers={"content-type": "application/json"})
    assert res.status_code == 422, res.text
    out = res.json()
    assert out["status"] == "ABSTAIN"
    assert out["refuse_reason"] == "caller_ontology_invalid:bad_number"
    assert out["values"] == []
    assert prompts == []


def test_declared_request_model_matches_the_frozen_contract_component(
    client: TestClient,
) -> None:
    """The wire extension must not drift contract/openapi-1.2.0.json.

    ``InsightsAskIn`` leaks into the contract spec's components (the route is
    not a contract route); ``InsightsWireIn`` must never appear there.
    """
    from pathlib import Path

    spec = json.loads(
        (Path(__file__).resolve().parents[2] / "contract" / "openapi-1.2.0.json").read_text(
            encoding="utf-8"
        )
    )
    frozen = spec["components"]["schemas"]["InsightsAskIn"]
    live = client.app.openapi()["components"]["schemas"]
    assert live["InsightsAskIn"] == frozen
    assert "InsightsWireIn" not in live
    assert set(InsightsAskIn.model_fields) == set(frozen["properties"])
    op = client.app.openapi()["paths"]["/v1/insights"]["post"]
    assert "ontology" in op["x-cortex-request-extension"]["fields"]
