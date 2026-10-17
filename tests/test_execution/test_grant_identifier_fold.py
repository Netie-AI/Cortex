"""GRANT-FOLD-01: grant matchers fold names exactly the way DuckDB resolves them.

DuckDB compares table, schema, column and CTE names case-insensitively for
ASCII letters only, quoted or not. Python's ``lower()`` folds non-ASCII case
too, so a grant on ``äpfel`` used to accept a reference to the different table
``"Äpfel"`` and DuckDB served that ungranted table's rows. Same for the Kelvin
sign (U+212A) against a grant on ``kelvin``.

The ground-truth test pins the helper to real DuckDB resolution, so a DuckDB
upgrade that changes the rule fails here first. The matcher tests run the SQL
the gate lets through on a real database and assert the rows that come back.
Positive controls: ``"äpfel"`` under a grant on ``äpfel``, a CJK name, and
``"ORDERS"`` under a grant on ``orders`` keep working.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest
from cortex_contract.answer import AskPayload
from cortex_contract.execution import Manifest

from CortexOS.dms import plan_sql_ask
from CortexOS.execution.manifest import (
    ManifestMalformed,
    PathNotAllowed,
    VerifiedManifest,
    enforce_manifest,
)
from CortexOS.execution.submit import execute_sql
from CortexOS.insights.caller_sql import validate_caller_sql
from CortexOS.plan_sql import payload as plan_payload

KELVIN = "K"  # KELVIN SIGN; Python lower() maps it to ASCII "k"
UPPER_A_UMLAUT = "Ä"  # LATIN CAPITAL LETTER A WITH DIAERESIS
APFEL_UPPER = f"{UPPER_A_UMLAUT}pfel"  # a different DuckDB table from "äpfel"
KELVIN_TABLE = f"{KELVIN}elvin"  # a different DuckDB table from "kelvin"
CJK = "人员"

# (table created, its marker row)
TABLES = {
    "äpfel": "granted-apfel",
    APFEL_UPPER: "UNGRANTED-APFEL",
    "kelvin": "granted-kelvin",
    KELVIN_TABLE: "UNGRANTED-KELVIN",
    CJK: "granted-cjk",
}


def _verified(predicates: dict[str, str]) -> VerifiedManifest:
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="sess-fold",
        org_id="acme",
        pool_id="pool-fold",
        issuer_key_id="int-1",
        allowed_paths=[],
        row_predicates=predicates,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="int-1", verified_at=now)


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "fold.duckdb"
    con = duckdb.connect(str(path))
    try:
        for name, marker in TABLES.items():
            con.execute(f'CREATE TABLE "{name}" (v VARCHAR)')
            con.execute(f'INSERT INTO "{name}" VALUES (?)', [marker])
        con.execute("CREATE TABLE orders (id INTEGER, tenant_id VARCHAR)")
        con.execute("INSERT INTO orders VALUES (1, 'a'), (2, 'b')")
    finally:
        con.close()
    return path


def _rows(warehouse: Path, sql: str, grant: dict[str, str]) -> list[dict]:
    rows, _, _ = execute_sql(_verified(grant), sql, db_path=warehouse, explain_gate=False)
    return rows


# -- ground truth: what DuckDB actually resolves ---------------------------------


@pytest.mark.parametrize(
    ("reference", "resolves_to"),
    [
        ('"äpfel"', "granted-apfel"),
        (f'"{APFEL_UPPER}"', "UNGRANTED-APFEL"),
        (APFEL_UPPER, "UNGRANTED-APFEL"),
        (f"{UPPER_A_UMLAUT}PFEL", "UNGRANTED-APFEL"),  # only ASCII letters fold
        ("KELVIN", "granted-kelvin"),
        (f'"{KELVIN_TABLE}"', "UNGRANTED-KELVIN"),
        (CJK, "granted-cjk"),
    ],
)
def test_duckdb_resolves_names_by_ascii_case_only(
    warehouse: Path, reference: str, resolves_to: str
) -> None:
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        assert con.execute(f"SELECT v FROM {reference}").fetchall() == [(resolves_to,)]
    finally:
        con.close()


# -- manifest enforcer (the executor's gate) --------------------------------------


@pytest.mark.parametrize(
    ("sql", "grant"),
    [
        (f'SELECT v FROM "{APFEL_UPPER}"', {"äpfel": "TRUE"}),
        (f"SELECT v FROM {UPPER_A_UMLAUT}PFEL", {"äpfel": "TRUE"}),
        (f'SELECT v FROM "{KELVIN_TABLE}"', {"kelvin": "TRUE"}),
        (f'SELECT v FROM main."{KELVIN_TABLE}"', {"kelvin": "TRUE"}),
    ],
)
def test_manifest_refuses_a_table_that_only_unicode_case_folds_to_the_grant(
    warehouse: Path, sql: str, grant: dict[str, str]
) -> None:
    with pytest.raises(PathNotAllowed):
        _rows(warehouse, sql, grant)


@pytest.mark.parametrize(
    ("sql", "grant", "expected"),
    [
        ('SELECT v FROM "äpfel"', {"äpfel": "TRUE"}, [{"v": "granted-apfel"}]),
        ("SELECT v FROM äpfel", {"äpfel": "TRUE"}, [{"v": "granted-apfel"}]),
        (f'SELECT v FROM "{APFEL_UPPER}"', {APFEL_UPPER: "TRUE"}, [{"v": "UNGRANTED-APFEL"}]),
        (f"SELECT v FROM {CJK}", {CJK: "TRUE"}, [{"v": "granted-cjk"}]),
        ("SELECT v FROM KELVIN", {"kelvin": "TRUE"}, [{"v": "granted-kelvin"}]),
        ('SELECT id FROM "ORDERS"', {"orders": "tenant_id = 'a'"}, [{"id": 1}]),
        ("SELECT id FROM main.ORDERS", {"Orders": "tenant_id = 'a'"}, [{"id": 1}]),
    ],
)
def test_manifest_positive_controls_return_the_granted_rows(
    warehouse: Path, sql: str, grant: dict[str, str], expected: list[dict]
) -> None:
    assert _rows(warehouse, sql, grant) == expected


def test_manifest_predicate_applies_under_any_ascii_spelling(warehouse: Path) -> None:
    out = enforce_manifest('SELECT id FROM "ORDERS"', _verified({"orders": "tenant_id = 'a'"}))
    assert "tenant_id = 'a'" in out
    assert _rows(warehouse, 'SELECT id FROM "ORDERS"', {"orders": "tenant_id = 'a'"}) == [{"id": 1}]


def test_manifest_refuses_grant_keys_that_fold_together_with_different_predicates() -> None:
    """``orders`` and ``ORDERS`` are one DuckDB table; which predicate wins is not guessed."""
    verified = _verified({"orders": "tenant_id = 'a'", "ORDERS": "TRUE"})
    with pytest.raises(ManifestMalformed):
        enforce_manifest("SELECT id FROM orders", verified)


# -- caller-declared catalog (/v1/insights source=space) ---------------------------


def _caller_rows(warehouse: Path, sql: str, declared: dict[str, list[str]]) -> list[tuple]:
    result = validate_caller_sql(sql, declared)
    assert result["ok"], result
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        return con.execute(result["sql"]).fetchall()
    finally:
        con.close()


@pytest.mark.parametrize(
    ("sql", "declared"),
    [
        (f'SELECT v FROM "{APFEL_UPPER}"', {"äpfel": []}),
        (f'SELECT v FROM "{KELVIN_TABLE}"', {"kelvin": ["v"]}),
        (f'SELECT v FROM main."{KELVIN_TABLE}"', {"main.kelvin": []}),
    ],
)
def test_caller_sql_refuses_a_table_that_only_unicode_case_folds_to_a_declared_one(
    sql: str, declared: dict[str, list[str]]
) -> None:
    result = validate_caller_sql(sql, declared)
    assert result["ok"] is False, result
    assert result["sql"] is None


def test_caller_sql_refuses_a_column_that_only_unicode_case_folds_to_a_declared_one() -> None:
    result = validate_caller_sql(f'SELECT "{UPPER_A_UMLAUT}col" FROM t', {"t": ["äcol", "id"]})
    assert result["ok"] is False, result
    assert "not declared" in result["reason"]


@pytest.mark.parametrize(
    ("sql", "declared", "expected"),
    [
        ('SELECT v FROM "äpfel"', {"äpfel": ["v"]}, [("granted-apfel",)]),
        (f"SELECT v FROM {CJK}", {CJK: ["v"]}, [("granted-cjk",)]),
        ('SELECT id FROM "ORDERS" ORDER BY id', {"orders": ["id"]}, [(1,), (2,)]),
        ("SELECT ID FROM orders ORDER BY ID", {"ORDERS": ["Id"]}, [(1,), (2,)]),
    ],
)
def test_caller_sql_positive_controls_return_rows(
    warehouse: Path, sql: str, declared: dict[str, list[str]], expected: list[tuple]
) -> None:
    assert _caller_rows(warehouse, sql, declared) == expected


# -- plan+SQL payload and its served-SQL table checks ------------------------------


def _request(*tables: str) -> plan_payload.PlanSqlRequest:
    body = {"tables": [{"name": t, "columns": [{"name": "v"}]} for t in tables]}
    return plan_payload.PlanSqlRequest(question="q", payload=AskPayload.model_validate(body))


def test_payload_widening_refuses_a_table_that_only_unicode_case_folds_to_the_grant() -> None:
    assert plan_payload.widening(_request(APFEL_UPPER), ["äpfel"]) == [
        f"{plan_payload.PAYLOAD_WIDENS_GRANT}:{APFEL_UPPER}"
    ]
    assert plan_payload.widening(_request(KELVIN_TABLE), ["kelvin"]) != []


@pytest.mark.parametrize(
    ("table", "grant"),
    [("äpfel", "äpfel"), (CJK, CJK), ("ORDERS", "orders"), ("orders", "Orders")],
)
def test_payload_widening_positive_controls(table: str, grant: str) -> None:
    assert plan_payload.widening(_request(table), [grant]) == []


def test_plan_sql_served_tables_fold_like_duckdb() -> None:
    assert plan_sql_ask._sql_tables(f'SELECT v FROM "{APFEL_UPPER}"') == {APFEL_UPPER}
    assert plan_sql_ask._sql_tables('SELECT id FROM "ORDERS"') == {"orders"}
    assert plan_sql_ask._ungranted(f'SELECT v FROM "{APFEL_UPPER}"', {"äpfel"}) is True
    assert plan_sql_ask._ungranted(f'SELECT v FROM "{KELVIN_TABLE}"', {"kelvin"}) is True
    assert plan_sql_ask._ungranted('SELECT v FROM "äpfel"', {"äpfel"}) is False
    assert plan_sql_ask._ungranted(f"SELECT v FROM {CJK}", {CJK}) is False
    assert plan_sql_ask._ungranted('SELECT id FROM "ORDERS"', {"orders"}) is False


@pytest.mark.parametrize(
    ("grant", "table", "widens"),
    [
        ({APFEL_UPPER: "TRUE"}, APFEL_UPPER, False),
        ({"äpfel": "TRUE"}, "äpfel", False),
        ({"orders": "TRUE"}, "ORDERS", False),
        ({CJK: "TRUE"}, CJK, False),
        ({"äpfel": "TRUE"}, APFEL_UPPER, True),
        ({APFEL_UPPER: "TRUE"}, "äpfel", True),
        ({"kelvin": "TRUE"}, KELVIN_TABLE, True),
    ],
)
def test_plan_sql_grant_names_keep_duckdb_identity(
    grant: dict[str, str], table: str, widens: bool
) -> None:
    granted = plan_sql_ask._granted_names(_verified(grant))
    assert bool(plan_payload.widening(_request(table), granted)) is widens


# -- /v1/insights caller catalog as the climb hands it over ------------------------


def _caller_ranking(*tables: str) -> dict:
    from CortexOS.insights.caller_ontology import SOURCE_CALLER

    return {
        "source": SOURCE_CALLER,
        "locations": [{"id": t, "where": {"table": t, "columns": ["v"]}} for t in tables],
    }


@pytest.mark.parametrize(
    ("declared", "sql", "ok"),
    [
        (APFEL_UPPER, f'SELECT v FROM "{APFEL_UPPER}"', True),
        ("äpfel", 'SELECT v FROM "äpfel"', True),
        (CJK, f"SELECT v FROM {CJK}", True),
        ("orders", 'SELECT v FROM "ORDERS"', True),
        ("äpfel", f'SELECT v FROM "{APFEL_UPPER}"', False),
        (APFEL_UPPER, 'SELECT v FROM "äpfel"', False),
        ("kelvin", f'SELECT v FROM "{KELVIN_TABLE}"', False),
    ],
)
def test_insights_caller_catalog_keeps_duckdb_identity(declared: str, sql: str, ok: bool) -> None:
    from CortexOS.crew import cot_climb

    ranking = _caller_ranking(declared)
    allowed = cot_climb._allowed_tables(ranking)
    columns = cot_climb._ranked_columns(ranking)
    result = cot_climb._check_sql(None, sql, ranking, allowed, columns)
    assert result["ok"] is ok, result
