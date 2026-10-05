"""#277 Epic amendment (2026-10-01) item 2: sqlglot owns statement boundaries.

A regex only finds the fenced block (and keyword start offsets); whether there
is exactly one statement, where it ends, and whether it is a SELECT /
WITH-SELECT come from ``sqlglot.parse(..., read="duckdb")``.
"""

from __future__ import annotations

import inspect

import pytest

from CortexOS.dms import sql_extract
from CortexOS.dms.sql_extract import extract_select, extract_statement

TWO_CTES = (
    "WITH a AS (SELECT county, cdscode FROM bronze.schools WHERE county = 'Alameda'), "
    "b AS (SELECT a.county, COUNT(*) AS n FROM a GROUP BY a.county) "
    "SELECT county, n FROM b ORDER BY n DESC"
)


def test_hand_written_statement_scanner_is_gone() -> None:
    src = inspect.getsource(sql_extract)
    for name in ("_scan", "_STATEMENT_START", "_WITH_HEAD", "_after_statement"):
        assert name not in src, f"{name} still parses statements by hand"
    assert "sqlglot.parse(" in src


@pytest.mark.parametrize(
    "text",
    [
        TWO_CTES,
        TWO_CTES + ";",
        f"```sql\n{TWO_CTES}\n```",
        f"```duckdb\n{TWO_CTES};\n```",
        f"Here is the query you asked for:\n```sql\n{TWO_CTES}\n```\nIt counts schools.",
        f"Sure. {TWO_CTES}; This returns one row per county.",
    ],
    ids=["bare", "bare_semicolon", "fenced", "fenced_duckdb", "fenced_with_prose", "unfenced_prose"],
)
def test_top_level_with_two_ctes_extracts_whole(text: str) -> None:
    assert extract_select(text) == TWO_CTES


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (
            "```sql\nSELECT a FROM t;\nSELECT b FROM u\n```",
            "more than one statement in model output (trailing SELECT refused)",
        ),
        (
            "```sql\nDROP TABLE t;\nSELECT a FROM t\n```",
            "more than one statement in model output (leading DROP refused)",
        ),
        (
            f"{TWO_CTES}; DROP TABLE bronze.schools",
            "more than one statement in model output (trailing DROP refused)",
        ),
        (
            f"DROP TABLE bronze.schools; {TWO_CTES}",
            "more than one statement in model output (leading DROP refused)",
        ),
        (
            "SELECT a FROM t;\nselect b from u",
            "more than one statement in model output (trailing SELECT refused)",
        ),
    ],
    ids=["fenced_trailing", "fenced_leading", "unfenced_trailing", "unfenced_leading", "lower"],
)
def test_two_statements_refuse_by_name(text: str, reason: str) -> None:
    got = extract_statement(text)
    assert got.sql is None
    assert got.reason == reason


@pytest.mark.parametrize(
    ("dml", "kind"),
    [
        ("INSERT INTO bronze.schools SELECT * FROM a", "INSERT"),
        ("DELETE FROM bronze.schools WHERE cdscode IN (SELECT cdscode FROM a)", "DELETE"),
        ("UPDATE bronze.schools SET county = 'x' FROM a WHERE a.cdscode = schools.cdscode", "UPDATE"),
    ],
    ids=["insert", "delete", "update"],
)
@pytest.mark.parametrize("fenced", [True, False], ids=["fenced", "unfenced"])
def test_ddl_or_dml_behind_a_with_refuses(dml: str, kind: str, fenced: bool) -> None:
    sql = f"WITH a AS (SELECT cdscode FROM bronze.schools) {dml}"
    text = f"```sql\n{sql}\n```" if fenced else sql
    got = extract_statement(text)
    assert got.sql is None
    assert got.reason == f"not a SELECT query ({kind} refused)"


def test_prose_around_the_fence_is_ignored() -> None:
    text = (
        "I looked at the schema. With this table you can count schools.\n"
        "```sql\nSELECT COUNT(*) AS n FROM bronze.schools\n```\n"
        "Select a different county if you need one; this is just an example."
    )
    assert extract_select(text) == "SELECT COUNT(*) AS n FROM bronze.schools"


def test_hostile_size_is_bounded() -> None:
    got = extract_statement("WITH a AS (" * 20_000)
    assert got.sql is None
    assert got.reason
