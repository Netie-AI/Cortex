"""A model's top-level ``WITH ... SELECT`` reaches the validator whole.

The extractor used to start at the first ``SELECT``, so
``WITH a AS (SELECT ..) SELECT ..`` was cut to the first CTE body plus a stray
``)`` and refused as a parse error. LLM-written BIRD SQL often starts with
``WITH``, so that cost answers. Now the whole single statement is extracted
from ``WITH``; the scope-based caller check then sees the full query:

* a legitimate WITH over the Space's tables answers, its SQL intact;
* a WITH whose CTE body reads an undeclared table is a named REFUSE;
* a WITH followed by a second statement (``DROP``, another ``SELECT``) is a
  named REFUSE -- never silently trimmed to the first statement;
* ``WITH RECURSIVE`` stays refused on the caller path.

Each case is POSTed to ``/v1/insights`` exactly as DMS sends it (source=space
caller catalog) with a stubbed ``complete()``, and asserted on the customer
envelope: status, badge, values, answer text, and the returned SQL run on DuckDB.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from CortexOS.dms.sql_extract import extract_select, extract_statement
from CortexOS.insights.caller_sql import validate_caller_sql
from tests.dms import test_insights_caller_ontology as _base
from tests.dms.test_insights_caller_ontology import (
    Q,
    _assert_refused,
    _gen_prompts,
    _post,
    dms_body,
    space_ontology,
)
from tests.dms.test_insights_caller_scope import SECRET, _lake

#: The same app fixture the INSIGHTS-ONTO route tests use.
client = _base.client

LEGIT_WITH = (
    "WITH alameda AS (\n"
    "  SELECT s.cdscode FROM bronze.schools AS s WHERE s.county = 'Alameda'\n"
    ")\n"
    "SELECT MAX(t.avgscrmath) AS top_math\n"
    "FROM bronze.satscores AS t JOIN alameda AS a ON t.cds = a.cdscode"
)
HIDDEN_TABLE_WITH = "WITH a AS (SELECT id FROM secrets) SELECT MAX(id) AS top_math FROM a"
RECURSIVE_WITH = (
    "WITH RECURSIVE secrets AS (SELECT id FROM secrets UNION ALL "
    "SELECT id + 1 FROM secrets WHERE id < 0) SELECT MAX(id) AS top_math FROM secrets"
)


def fenced(sql: str, after: str = "") -> str:
    return f"Here is the query.\n\n```sql\n{sql};\n{after}```\n\nIt joins the two tables."


def unfenced(sql: str, after: str = "") -> str:
    return f"Here is the query, with a join:\n{sql};{after}"


FORMS = {"fenced": fenced, "unfenced": unfenced}


# -- through /v1/insights ------------------------------------------------------


@pytest.mark.parametrize("form", list(FORMS))
def test_legit_top_level_with_answers_with_its_sql_intact(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    assert _lake().execute(LEGIT_WITH).fetchall() == [(640,)]
    text = FORMS[form](LEGIT_WITH)
    res, prompts = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=text)
    assert res.status_code == 200, res.text
    body = res.json()
    assert _gen_prompts(prompts)
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert "Validated SQL" in body["answer"], body["answer"]
    assert body["generative"]["check"] == "caller catalog: tables and declared columns"
    returned = body["query_sql"]
    assert returned.upper().startswith("WITH ALAMEDA AS ("), returned
    assert "JOIN alameda AS a" in returned
    # Intact: the returned statement runs and gives the same answer as the model's.
    assert _lake().execute(returned).fetchall() == [(640,)]
    # The whole model statement reached the validator: its output for exactly
    # LEGIT_WITH is what came back (only the LIMIT cap added).
    declared = {
        "bronze.schools": ["cdscode", "county", "school"],
        "bronze.satscores": ["cds", "avgscrmath", "numtsttakr"],
    }
    assert validate_caller_sql(LEGIT_WITH, declared)["sql"] == returned
    assert returned.endswith(" LIMIT 1000"), returned


@pytest.mark.parametrize("form", list(FORMS))
def test_with_hiding_an_undeclared_table_is_a_named_refuse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    assert _lake().execute(HIDDEN_TABLE_WITH).fetchall() == [(SECRET,)]
    res, prompts = _post(
        client, monkeypatch, dms_body(Q, space_ontology()), sql=FORMS[form](HIDDEN_TABLE_WITH)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert _gen_prompts(prompts)
    _assert_refused(body, "table secrets is not in the caller catalog")
    assert "parse error" not in body["answer"]
    assert str(SECRET) not in json.dumps(body)


@pytest.mark.parametrize(
    ("form", "after", "word"),
    [
        ("fenced", "DROP TABLE bronze.schools;\n", "DROP"),
        ("fenced", "SELECT id FROM secrets;\n", "SELECT"),
        ("unfenced", " DROP TABLE bronze.schools;", "DROP"),
        ("unfenced", "\nINSERT INTO bronze.schools VALUES ('9','X','Z');", "INSERT"),
        ("unfenced", " select id from secrets", "SELECT"),
    ],
    ids=["fenced_drop", "fenced_select", "unfenced_drop", "unfenced_insert", "unfenced_select"],
)
def test_with_followed_by_a_second_statement_is_a_named_refuse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, form: str, after: str, word: str
) -> None:
    text = FORMS[form](LEGIT_WITH, after)
    res, prompts = _post(client, monkeypatch, dms_body(Q, space_ontology()), sql=text)
    assert res.status_code == 200, res.text
    body = res.json()
    assert _gen_prompts(prompts)
    _assert_refused(body, f"more than one statement in model output (trailing {word} refused)")
    assert "640" not in json.dumps(body["values"])


@pytest.mark.parametrize("form", list(FORMS))
def test_top_level_with_recursive_stays_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    assert _lake().execute(RECURSIVE_WITH).fetchall() == [(SECRET,)]
    res, _ = _post(
        client, monkeypatch, dms_body(Q, space_ontology()), sql=FORMS[form](RECURSIVE_WITH)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_refused(body, "recursive CTE refused")
    assert str(SECRET) not in json.dumps(body)


# -- extract_select unit -------------------------------------------------------


@pytest.mark.parametrize("form", list(FORMS))
def test_extract_takes_the_whole_with_statement(form: str) -> None:
    assert extract_select(FORMS[form](LEGIT_WITH)) == LEGIT_WITH
    assert extract_select(FORMS[form](RECURSIVE_WITH)) == RECURSIVE_WITH


def test_extract_with_column_list_and_materialized_hint() -> None:
    sql = "WITH a(x) AS MATERIALIZED (SELECT 1 FROM t), b AS (SELECT x FROM a) SELECT x FROM b"
    assert extract_select(f"```sql\n{sql}\n```") == sql
    assert extract_select(f"sure -- {sql.lower()}") == sql.lower()


def test_prose_with_is_not_a_statement_start() -> None:
    text = "I answered this with care. Use a filter with the county.\nSELECT a FROM t"
    assert extract_select(text) == "SELECT a FROM t"


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("WITH a AS (SELECT 1 FROM t) SELECT * FROM a; DROP TABLE t", "DROP"),
        ("WITH a AS (SELECT 1 FROM t) SELECT * FROM a;\n-- next\nDELETE FROM t;", "DELETE"),
        ("```sql\nSELECT a FROM t;\nSELECT b FROM u;\n```", "SELECT"),
        ("```sql\nSELECT a FROM t; oops\n```", "text"),
        ("SELECT a FROM t; ATTACH 'x.db'", "ATTACH"),
    ],
)
def test_extract_refuses_a_second_statement_by_name(text: str, word: str) -> None:
    got = extract_statement(text)
    assert got.sql is None
    assert got.reason == f"more than one statement in model output (trailing {word} refused)"
    assert extract_select(text) is None


def test_extract_keeps_trailing_prose_and_comments_outside_a_fence() -> None:
    assert extract_select("SELECT a FROM t; This returns a.") == "SELECT a FROM t"
    assert extract_select("SELECT a FROM t; Show this to the user.") == "SELECT a FROM t"
    assert extract_select("```sql\nSELECT a FROM t; -- done\n```") == "SELECT a FROM t"
    assert extract_select("SELECT a FROM t;;") == "SELECT a FROM t"


def test_semicolon_and_from_inside_literals_are_not_real() -> None:
    sql = "SELECT 'a;b' AS c, \"x;y\" FROM t WHERE d = 'it''s; fine'"
    assert extract_select(sql + ";") == sql
    assert extract_select("SELECT 'from' AS c") is None
    assert extract_select("SELECT 1 AS c -- from t") is None
    got = extract_statement("WITH a AS (SELECT 1 /* from */) SELECT * /* FROM */")
    assert got.sql is None and got.reason == "extracted query has no FROM"


def test_existing_extraction_shapes_still_hold() -> None:
    fence_only_select_list = (
        "```sql\nSELECT i.sku, i.sku_name\n```\nFROM inventory i WHERE i.quantity_kg > 0"
    )
    got = extract_select(fence_only_select_list)
    assert got is not None and "```" not in got and "FROM inventory i" in got
    assert extract_select("SELECT i.sku, i.sku_name") is None
    assert extract_select("there are 999 skus\n```sql\nSELECT sku FROM inventory\n```") == (
        "SELECT sku FROM inventory"
    )
    assert extract_statement("").reason == "empty model output"
    assert extract_select("no query here") is None
