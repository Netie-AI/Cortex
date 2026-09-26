"""INSIGHTS-ONTO round 3: caller SQL is checked by scope, not by name.

Root-cause class: "is this name a CTE" was decided by collecting ``WITH`` names
from the whole tree, so a CTE declared in one scope exempted a real table of the
same name in another. ``validate_caller_sql`` now asks sqlglot's scope builder
which references are CTEs; every other table must be in the caller catalog.

Each escape below is first run on DuckDB to show it really reads the undeclared
``main.secrets`` table, then POSTed exactly as DMS sends it. The customer
envelope must be a named REFUSE with no SQL and no values -- never a green
"Validated SQL" over a query that reads outside the Space.

Also covered: a quoted single-part name containing a dot, a SELECT alias that
hides an undeclared real column, the LIMIT cap the engine-pack path already
applies, and oversized / non-finite / non-numeric numeric fields (a named
4xx, never a 500).
"""

from __future__ import annotations

import json
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from CortexOS.insights.caller_sql import validate_caller_sql
from tests.dms import test_insights_caller_ontology as _base
from tests.dms.test_insights_caller_ontology import (
    GOOD_SQL,
    Q,
    _assert_refused,
    _gen_prompts,
    _post,
    _stub,
    dms_body,
    space_ontology,
)

#: The same app fixture the INSIGHTS-ONTO route tests use.
client = _base.client

SECRET = 424242

CTE_ESCAPES = {
    "cte_in_derived_table_shadows_outer_real_table": (
        "SELECT MAX(s.id) AS top_math FROM "
        "(WITH secrets AS (SELECT 1 AS id) SELECT id FROM secrets) AS d, secrets AS s"
    ),
    "forward_reference_to_later_cte": (
        "SELECT MAX(id) AS top_math FROM (WITH a AS (SELECT id FROM secrets), "
        "secrets AS (SELECT 1 AS id) SELECT id FROM a) AS d"
    ),
    "same_named_cte_and_real_table_in_different_scopes": (
        "SELECT (SELECT MAX(id) FROM secrets) AS top_math "
        "FROM (WITH secrets AS (SELECT 1 AS id) SELECT id FROM secrets) AS d"
    ),
}

RECURSIVE_ESCAPE = (
    "SELECT MAX(id) AS top_math FROM (WITH RECURSIVE secrets AS (SELECT id FROM secrets "
    "UNION ALL SELECT id + 1 FROM secrets WHERE id < 0) SELECT id FROM secrets) AS d"
)
# The same escapes as top-level statements, for the validator itself (the route
# path for top-level WITH is covered in test_insights_with_extract.py).
TOP_LEVEL = [
    "WITH a AS (SELECT id FROM secrets), secrets AS (SELECT 1 AS id) SELECT MAX(id) FROM a",
    "WITH RECURSIVE secrets AS (SELECT id FROM secrets UNION ALL "
    "SELECT id + 1 FROM secrets WHERE id < 0) SELECT MAX(id) FROM secrets",
]


def _lake() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA bronze")
    con.execute(
        "CREATE TABLE bronze.schools "
        "(cdscode VARCHAR, county VARCHAR, school VARCHAR, phone VARCHAR)"
    )
    con.execute(
        "INSERT INTO bronze.schools VALUES "
        "('1','Alameda','A','555-0101'),('2','Alameda','B','555-0102'),('3','Fresno','C','555-0103')"
    )
    con.execute(
        "CREATE TABLE bronze.satscores (cds VARCHAR, avgscrmath INTEGER, numtsttakr INTEGER)"
    )
    con.execute("INSERT INTO bronze.satscores VALUES ('1',512,40),('2',640,55),('3',700,10)")
    con.execute("CREATE TABLE main.secrets (id INTEGER)")
    con.execute(f"INSERT INTO main.secrets VALUES ({SECRET})")
    con.execute('CREATE TABLE main."bronze.schools" (school VARCHAR)')
    con.execute("INSERT INTO main.\"bronze.schools\" VALUES ('decoy-dotted')")
    return con


def _post_raw(client: TestClient, monkeypatch: pytest.MonkeyPatch, raw: str) -> tuple[Any, list]:
    from CortexOS.crew import freeroute as fr

    prompts: list[str] = []
    monkeypatch.setattr(fr, "complete", _stub(GOOD_SQL, prompts))
    safe = TestClient(client.app, client=("127.0.0.1", 5555), raise_server_exceptions=False)
    res = safe.post("/v1/insights", content=raw, headers={"content-type": "application/json"})
    return res, prompts


# -- CTE scope escapes --------------------------------------------------------


@pytest.mark.parametrize("sql", list(CTE_ESCAPES.values()), ids=list(CTE_ESCAPES))
def test_cte_outside_its_scope_does_not_exempt_the_real_table(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, sql: str
) -> None:
    assert _lake().execute(sql).fetchall() == [(SECRET,)]  # DuckDB reads main.secrets
    res, prompts = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=sql)
    assert res.status_code == 200, res.text
    body = res.json()
    assert _gen_prompts(prompts)
    _assert_refused(body, "table secrets is not in the caller catalog")
    assert str(SECRET) not in json.dumps(body)


def test_recursive_cte_is_a_named_refuse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _lake().execute(RECURSIVE_ESCAPE).fetchall() == [(SECRET,)]
    res, _ = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=RECURSIVE_ESCAPE)
    assert res.status_code == 200, res.text
    _assert_refused(res.json(), "recursive CTE refused")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT o.x AS top_math FROM bronze.schools AS s, LATERAL (SELECT 1 AS x) AS o",
        "SELECT x AS top_math FROM UNNEST([1]) AS t(x)",
    ],
    ids=["lateral", "unnest"],
)
def test_constructs_the_scope_check_does_not_model_are_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, sql: str
) -> None:
    res, _ = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=sql)
    assert res.status_code == 200, res.text
    _assert_refused(res.json(), "refused")


def test_cte_bound_in_its_own_scope_is_still_accepted_and_reads_the_space(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sql = (
        "SELECT MAX(d.avgscrmath) AS top_math FROM (WITH alameda AS "
        "(SELECT s.cdscode FROM bronze.schools AS s WHERE s.county = 'Alameda') "
        "SELECT t.avgscrmath FROM bronze.satscores AS t "
        "JOIN alameda AS a ON t.cds = a.cdscode) AS d"
    )
    res, _ = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=sql)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert "Validated SQL" in body["answer"]
    assert body["generative"]["check"] == "caller catalog: tables and declared columns"
    assert _lake().execute(body["query_sql"]).fetchall() == [(640,)]


# -- a dot inside one identifier part ------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT MAX(school) AS top_math FROM "bronze.schools"',
        'SELECT MAX(school) AS top_math FROM main."bronze.schools"',
    ],
    ids=["bare_dotted", "main_dotted"],
)
def test_quoted_dotted_name_is_not_the_declared_schema_table(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, sql: str
) -> None:
    assert _lake().execute(sql).fetchall() == [("decoy-dotted",)]
    res, _ = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=sql)
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_refused(body, "contains a dot")
    assert "decoy-dotted" not in json.dumps(body)


# -- a SELECT alias does not hide an undeclared real column --------------------


def test_alias_named_like_an_undeclared_column_is_still_checked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sql = "SELECT school AS phone FROM bronze.schools WHERE phone = '555-0102'"
    # DuckDB's WHERE binds ``phone`` to the undeclared real column, not the alias.
    assert _lake().execute(sql).fetchall() == [("B",)]
    res, _ = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=sql)
    assert res.status_code == 200, res.text
    _assert_refused(res.json(), "column phone is not declared")


def test_star_relation_does_not_launder_an_undeclared_column(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sql = "SELECT MAX(d.phone) AS top_math FROM (SELECT * FROM bronze.schools) AS d"
    assert _lake().execute(sql).fetchall() == [("555-0103",)]
    res, _ = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=sql)
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_refused(body, "column d.phone is not declared")
    assert "555-0103" not in json.dumps(body)


def test_order_by_a_select_alias_is_still_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sql = "SELECT county AS top_math FROM bronze.schools ORDER BY top_math LIMIT 1"
    res, _ = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=sql)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ABSTAIN"
    assert "Validated SQL" in body["answer"]
    assert _lake().execute(body["query_sql"]).fetchall() == [("Alameda",)]


# -- LIMIT cap (same as sql_guardrail.safe_sql) --------------------------------


@pytest.mark.parametrize(
    ("limit", "expected"),
    [("", " LIMIT 1000"), (" LIMIT 5000", " LIMIT 1000"), (" LIMIT 7", " LIMIT 7")],
    ids=["missing", "over_cap", "under_cap"],
)
def test_caller_sql_is_capped_like_the_engine_pack_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, limit: str, expected: str
) -> None:
    sql = "SELECT s.cdscode, s.county FROM bronze.schools AS s" + limit
    res, _ = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=sql)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["query_sql"].endswith(expected), body["query_sql"]
    con = _lake()
    con.execute("INSERT INTO bronze.schools SELECT i::VARCHAR, 'X', 'S', '' FROM range(1500) t(i)")
    assert len(con.execute(body["query_sql"]).fetchall()) <= 1000


def test_validator_unit_cap_and_refusals() -> None:
    catalog = {"bronze.schools": ["cdscode", "county", "school"]}
    ok = validate_caller_sql("SELECT county FROM bronze.schools LIMIT 99999", catalog)
    assert ok["ok"] and ok["sql"].endswith("LIMIT 1000")
    escapes = list(CTE_ESCAPES.values()) + TOP_LEVEL
    for sql in escapes + [RECURSIVE_ESCAPE, 'SELECT * FROM "bronze.schools"']:
        if "secrets" in sql:
            assert _lake().execute(sql).fetchall() == [(SECRET,)], sql
        out = validate_caller_sql(sql, catalog)
        assert out["ok"] is False and out["sql"] is None, sql


# -- numeric fuzz: a named 4xx, never a 500 -------------------------------------

_HUGE = "1" + "0" * 400
_NUMERIC_FUZZ = [_HUGE, "-" + _HUGE, "1" + "0" * 30, "NaN", "Infinity", "-Infinity", "1e400"]


_FUZZ_IDS = ["int400", "neg_int400", "int31", "nan", "inf", "neg_inf", "float_overflow"]


@pytest.mark.parametrize(
    "raw_score",
    _NUMERIC_FUZZ + ['"5"', "true", "null", "[]"],
    ids=_FUZZ_IDS + ["string", "bool", "null", "list"],
)
def test_schema_score_fuzz_is_a_named_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, raw_score: str
) -> None:
    onto = json.dumps(space_ontology()).replace('"score": 4', f'"score": {raw_score}')
    assert raw_score in onto
    raw = json.dumps(dms_body(Q)).rstrip("}") + ', "ontology": ' + onto + "}"
    res, prompts = _post_raw(client, monkeypatch, raw)
    assert res.status_code == 422, res.text
    out = res.json()
    assert out["status"] == "ABSTAIN"
    assert out["refuse_reason"] in {
        "caller_ontology_invalid:bad_number",
        "caller_ontology_invalid:bad_shape",
    }
    assert out["values"] == []
    assert prompts == []


@pytest.mark.parametrize("raw", _NUMERIC_FUZZ + ['"x"'], ids=_FUZZ_IDS + ["string"])
@pytest.mark.parametrize(
    "field",
    ["mode", "model_preference", "ranked_metric", "generate_retry", "query_plan", "intent_slots"],
)
def test_request_field_numeric_fuzz_never_500s(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, field: str, raw: str
) -> None:
    base = dms_body(Q, space_ontology())
    base.pop(field, None)
    body = json.dumps(base).rstrip("}") + f', "{field}": {raw}' + "}"
    res, prompts = _post_raw(client, monkeypatch, body)
    assert res.status_code < 500, res.text
    if res.status_code != 200:
        assert res.status_code in {400, 422}, res.text
        assert "Traceback" not in res.text


def test_int_too_long_to_parse_is_not_a_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    onto = json.dumps(space_ontology()).replace('"score": 4', '"score": 1' + "0" * 5000)
    raw = json.dumps(dms_body(Q)).rstrip("}") + ', "ontology": ' + onto + "}"
    res, prompts = _post_raw(client, monkeypatch, raw)
    assert 400 <= res.status_code < 500, res.text
    assert prompts == []
