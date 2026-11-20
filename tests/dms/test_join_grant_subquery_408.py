"""JOIN-GRANT-SUBQUERY-01 (#408). The join grant decides every relating shape.

``join_grant_corpus.json`` is replayed here in full, twice: against
``schema_context.ungranted_reason`` and against the envelope a consumer
receives from ``POST /v1/insights``. DMS executes ``sql_used``, so the
envelope assertions are the ones that matter: a refused statement is a named
REFUSE that carries no SQL, and is never retried.

Model calls are the FreeRoute ``complete`` seam. No network. Table names are
synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.insights import schema_context as schema_mod
from tests.dms import test_schema_context_01 as base
from tests.dms.test_schema_context_01 import api  # noqa: F401 - the route fixture

CORPUS = json.loads(
    (Path(__file__).parent / "join_grant_corpus.json").read_text(encoding="utf-8")
)
CASES: list[dict[str, Any]] = CORPUS["cases"]
SCHEMAS: dict[str, str] = CORPUS["schemas"]
UNGRANTED = "refuse_ungranted_join"
UNANALYSABLE = "refuse_unanalysable"
#: The shapes #408 names, plus the ones the class adds.
REQUIRED_SHAPES = {
    "cte",
    "derived_table",
    "in_subquery",
    "exists",
    "correlated_subquery",
    "set_operation",
    "lateral",
    "duckdb_form",
    "identifier_spelling",
    "statement_boundary",
    "unanalysable",
}


def _cases(*expected: str) -> list[Any]:
    return [pytest.param(c, id=c["name"]) for c in CASES if c["expected"] in expected]


def _schema(case: dict[str, Any]) -> str:
    return SCHEMAS[case.get("schema", "pair_no_join")]


def _reason(case: dict[str, Any]) -> str:
    if case["expected"] == UNANALYSABLE:
        return schema_mod.UNANALYSABLE_JOIN
    return "schema_context:ungranted_join:" + case["pair"]


def _ask(api: TestClient, monkeypatch: pytest.MonkeyPatch, schema: str, sql: str):  # noqa: F811
    return base._generate(api, monkeypatch, schema, lambda _prompt: sql)


# --- the corpus ----------------------------------------------------------


def test_corpus_covers_every_shape_and_names_each_case_once() -> None:
    assert len(CASES) >= 50, f"only {len(CASES)} cases"
    assert len({c["name"] for c in CASES}) == len(CASES), "duplicate case names"
    assert {c["expected"] for c in CASES} <= {UNGRANTED, UNANALYSABLE}
    assert {c["shape"] for c in CASES} >= REQUIRED_SHAPES
    for case in CASES:
        if case["expected"] == UNGRANTED:
            left, right = case["pair"].split("+")
            granted = schema_mod.parse_schema_context(SCHEMAS[case["schema"]]).joins
            assert (left, right) not in granted
            if "control_schema" in case:
                # The case is hostile only because the pair is missing, and
                # its control differs only by granting it.
                control = schema_mod.parse_schema_context(SCHEMAS[case["control_schema"]]).joins
                assert (left, right) in control


@pytest.mark.parametrize("case", _cases(UNGRANTED, UNANALYSABLE))
def test_must_fail_gate_names_the_refusal(case: dict[str, Any]) -> None:
    assert schema_mod.ungranted_reason(case["sql"], _schema(case)) == _reason(case)


@pytest.mark.parametrize(
    "case", [p for p in _cases(UNGRANTED) if "control_schema" in p.values[0]]
)
def test_must_pass_same_sql_is_decided_once_the_join_is_granted(case: dict[str, Any]) -> None:
    """The refusal comes from the missing JOINS line, not from the shape."""
    assert schema_mod.ungranted_reason(case["sql"], SCHEMAS[case["control_schema"]]) is None


@pytest.mark.parametrize("case", _cases(UNANALYSABLE))
def test_must_fail_unanalysable_is_refused_under_every_schema(case: dict[str, Any]) -> None:
    for name, schema in SCHEMAS.items():
        # Named unanalysable wherever its tables are declared; a refusal everywhere.
        assert schema_mod.ungranted_reason(case["sql"], schema) is not None, name
    for name in ("chain", "chain_all"):
        assert (
            schema_mod.ungranted_reason(case["sql"], SCHEMAS[name])
            == schema_mod.UNANALYSABLE_JOIN
        ), name


# --- what the consumer receives ------------------------------------------


@pytest.mark.parametrize("case", _cases(UNGRANTED, UNANALYSABLE))
def test_must_fail_envelope_is_a_named_refusal_with_no_sql(
    api: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, Any],
) -> None:
    sql = case["sql"]
    body, prompts = _ask(api, monkeypatch, _schema(case), sql)
    if case.get("envelope") == "refused_before_the_gate":
        # The statement extractor does not accept this spelling; still no SQL out.
        assert body["status"] == "REFUSE"
        assert not body.get("sql_used") and not (body.get("generative") or {}).get("sql")
        assert sql not in json.dumps(body)
        return
    base._assert_named_refusal(body, case.get("envelope_reason") or _reason(case), sql)
    assert not body.get("values")
    # Refused before any retry: the SQL never went back into a prompt.
    assert base._generate_count(prompts) == 1
    assert all(sql not in prompt for _purpose, prompt in prompts)


@pytest.mark.parametrize(
    "name",
    [
        "cte_join",
        "derived_table_join",
        "in_subquery",
        "exists_correlated",
        "scalar_subquery_correlated_in_select",
        "union_inside_derived_table",
    ],
)
def test_must_pass_envelope_serves_the_shape_once_the_join_is_granted(
    api: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """The fix narrows nothing that was granted: real analytics still leaves as sql_used."""
    case = next(c for c in CASES if c["name"] == name)
    body, prompts = _ask(api, monkeypatch, SCHEMAS[case["control_schema"]], case["sql"])
    assert body["status"] != "REFUSE", body.get("answer")
    served = schema_mod.schema_idents(str(body.get("sql_used") or ""))
    assert {"alpha_ledger", "beta_entry"} <= served
    assert base._generate_count(prompts) == 1


def test_guard_removed_connection_check_serves_the_cte_join(
    api: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the new rule off, the bypass is back: the CTE join leaves as sql_used."""
    case = next(c for c in CASES if c["name"] == "cte_join")
    monkeypatch.setattr(schema_mod, "_unconnected_pair", lambda tables, joined: None)
    body, _prompts = _ask(api, monkeypatch, _schema(case), case["sql"])
    assert {"alpha_ledger", "beta_entry"} <= schema_mod.schema_idents(
        str(body.get("sql_used") or "")
    )
    with pytest.raises(AssertionError):
        base._assert_named_refusal(body, _reason(case), case["sql"])


@pytest.mark.parametrize("case", _cases(UNANALYSABLE))
def test_must_fail_unanalysable_stays_refused_when_the_sql_gate_would_pass_it(
    api: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, Any],
) -> None:
    """The join refusal does not lean on the SQL gate: loosen that gate and it stands."""
    from CortexOS.insights import caller_sql

    sql = case["sql"]
    monkeypatch.setattr(
        caller_sql,
        "validate_caller_sql",
        lambda text, declared: {"ok": True, "sql": text, "tables": [], "reason": "", "check": "x"},
    )
    body, prompts = _ask(api, monkeypatch, _schema(case), sql)
    base._assert_named_refusal(body, schema_mod.UNANALYSABLE_JOIN, sql)
    assert base._generate_count(prompts) == 1


# --- nothing that was refused is served, nothing granted is refused -------

CHAIN = SCHEMAS["chain"]


@pytest.mark.parametrize(
    ("sql", "schema", "expected"),
    [
        # Granted shapes stay decided.
        ("SELECT qty FROM alpha_ledger WHERE qty > 1", "pair_no_join", None),
        (
            "SELECT l.qty FROM alpha_ledger l JOIN alpha_ledger r ON l.id = r.id WHERE l.qty > 1",
            "pair_no_join",
            None,
        ),
        (
            "WITH x AS (SELECT id, qty FROM alpha_ledger WHERE qty > 1) "
            "SELECT l.qty FROM x l JOIN x r ON l.id = r.id",
            "pair_no_join",
            None,
        ),
        (
            "SELECT a.qty, g.note FROM alpha_ledger a "
            "JOIN beta_entry b ON a.id = b.ref_code "
            "JOIN gamma_note g ON b.ref_code = g.entry_ref WHERE a.qty > 1",
            "chain",
            None,
        ),
        (
            "WITH ab AS (SELECT b.ref_code, a.qty FROM alpha_ledger a "
            "JOIN beta_entry b ON a.id = b.ref_code WHERE a.qty > 1) "
            "SELECT ab.qty, g.note FROM ab JOIN gamma_note g ON ab.ref_code = g.entry_ref",
            "chain",
            None,
        ),
        # The direct-join refusals main already made are unchanged, by name.
        (
            "SELECT a.qty FROM alpha_ledger a JOIN beta_entry b ON a.id = b.ref_code "
            "WHERE a.qty > 1",
            "pair_no_join",
            "schema_context:ungranted_join:alpha_ledger+beta_entry",
        ),
        (
            "SELECT a.qty FROM alpha_ledger a JOIN gamma_note g ON a.id = g.entry_ref "
            "JOIN beta_entry b ON a.id = b.ref_code WHERE a.qty > 1",
            "chain",
            "schema_context:ungranted_join:alpha_ledger+gamma_note",
        ),
        (
            "SELECT qty FROM alpha_ledger WHERE id IN (SELECT k FROM other_ledger)",
            "pair_no_join",
            "schema_context:ungranted_table:other_ledger",
        ),
        # A qualified name reaches the real table even when a CTE has its name.
        (
            "WITH ghost AS (SELECT 1 AS k) "
            "SELECT qty FROM alpha_ledger WHERE id IN (SELECT k FROM main.ghost)",
            "pair_no_join",
            "schema_context:ungranted_table:ghost",
        ),
    ],
)
def test_existing_decisions_are_unchanged(sql: str, schema: str, expected: str | None) -> None:
    assert schema_mod.ungranted_reason(sql, SCHEMAS[schema]) == expected


def test_unparsable_sql_is_left_to_the_sql_gate() -> None:
    assert schema_mod.ungranted_reason("SELECT FROM WHERE (", CHAIN) is None


@pytest.mark.parametrize("depth", [50, 200, 2000])
def test_must_fail_deep_nesting_is_refused_and_never_raises(depth: int) -> None:
    """Adversary round, lens 5: RecursionError escaped the gate as a crash."""
    from CortexOS.insights.caller_sql import validate_caller_sql

    sql = (
        "SELECT qty FROM alpha_ledger WHERE "
        + "(" * depth
        + "1 = 1"
        + ")" * depth
        + " AND id IN (SELECT ref_code FROM beta_entry)"
    )
    schema = SCHEMAS["pair_no_join"]
    reason = schema_mod.ungranted_reason(sql, schema)
    assert schema_mod.brute_force_reason(sql) in (None, schema_mod.BRUTE_FORCE_SCAN)
    if reason is None:
        # Unparsed here, so the SQL gate must be the one that refuses it.
        checked = validate_caller_sql(sql, schema_mod.parse_schema_context(schema).tables)
        assert checked["ok"] is False and checked["sql"] is None
    else:
        assert reason == "schema_context:ungranted_join:alpha_ledger+beta_entry"
