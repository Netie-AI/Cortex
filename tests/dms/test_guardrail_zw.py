"""JOIN-GRANT-ZW in the SQL guardrail: ``sql_guardrail.validate_sql`` gates SQL
that runs with no manifest behind it (``POST /dms/lakehouse/query`` takes the
caller's SQL; ``guard_and_execute`` runs the legacy path). A zero-width
character the parser keeps in an alias and DuckDB reads as whitespace hid a
table or a file reader from it. Found by the #408 verifier round.

Assertions are on what the caller receives: the guardrail result, the rows
``guard_and_execute`` returns, and the HTTP response of the lakehouse route.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from CortexOS.dms import sql_guardrail
from CortexOS.dms.sql_guardrail import guard_and_execute, validate_sql
from tests.dms.test_guardrail_table_functions import (  # noqa: F401
    SECRET,
    SEMANTIC,
    con,
    lake_client,
    secret_file,
)

ZW = "​"


def _hidden(secret_file: Path) -> dict[str, str]:  # noqa: F811
    return {
        "cross_join_secrets": f"SELECT * FROM inventory AS i{ZW}CROSS{ZW}JOIN{ZW}secrets",
        "union_secrets": (
            "SELECT sku FROM inventory AS i﻿UNION﻿ALL﻿SELECT﻿column0"
            "﻿FROM﻿secrets"
        ),
        "cross_join_file_reader": (
            f"SELECT * FROM inventory AS i{ZW}CROSS{ZW}JOIN{ZW}read_csv('{secret_file.as_posix()}')"
        ),
    }


NAMES = ["cross_join_secrets", "union_secrets", "cross_join_file_reader"]


@pytest.mark.parametrize("name", NAMES)
def test_duckdb_runs_the_hidden_read(name: str, con, secret_file: Path) -> None:  # noqa: F811
    """Each case is a query DuckDB answers with the secret, so the refusal below is real."""
    rows = con.execute(_hidden(secret_file)[name]).fetchall()
    assert SECRET in repr(rows)


@pytest.mark.parametrize("name", NAMES)
def test_must_fail_guardrail_refuses_by_name(name: str, secret_file: Path) -> None:  # noqa: F811
    result = validate_sql(_hidden(secret_file)[name], SEMANTIC)
    assert result.passed is False and result.safe_sql is None
    assert result.violations in (["HIDDEN_CODEPOINT:U+200B"], ["HIDDEN_CODEPOINT:U+FEFF"])


@pytest.mark.parametrize("name", NAMES)
def test_must_fail_guard_and_execute_returns_no_secret(
    name: str, con, secret_file: Path  # noqa: F811
) -> None:
    result, rows, _audit = guard_and_execute(_hidden(secret_file)[name], SEMANTIC, con)
    assert result.passed is False
    assert rows == []
    assert SECRET not in repr(rows)


@pytest.mark.parametrize("name", ["cross_join_secrets", "cross_join_file_reader"])
def test_guard_removed_the_secret_is_served(
    name: str, con, secret_file: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setattr(sql_guardrail, "hidden_codepoint", lambda sql: None)
    result, rows, _audit = guard_and_execute(_hidden(secret_file)[name], SEMANTIC, con)
    assert result.passed is True
    assert SECRET in repr(rows)


def test_must_fail_lakehouse_query_returns_no_hidden_table(lake_client) -> None:  # noqa: F811
    sql = f"SELECT sku FROM lake.silver.stock AS s{ZW}CROSS{ZW}JOIN{ZW}lake.silver.stock"
    res = lake_client.post("/dms/lakehouse/query", json={"sql": sql})
    assert res.status_code == 400, res.text
    body = res.json()
    assert body["detail"]["violations"] == ["HIDDEN_CODEPOINT:U+200B"]
    assert "rows" not in body


def test_must_fail_lakehouse_query_returns_no_file_content(
    lake_client, secret_file: Path  # noqa: F811
) -> None:
    sql = f"SELECT * FROM lake.silver.stock AS s{ZW}CROSS{ZW}JOIN{ZW}read_csv('{secret_file.as_posix()}')"
    res = lake_client.post("/dms/lakehouse/query", json={"sql": sql})
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["violations"] == ["HIDDEN_CODEPOINT:U+200B"]
    assert SECRET not in res.text


def test_must_pass_lakehouse_query_with_a_zwj_literal_is_served(lake_client) -> None:  # noqa: F811
    """A ZWJ inside a literal is data both parsers agree on, not a hidden token."""
    res = lake_client.post(
        "/dms/lakehouse/query",
        json={"sql": "SELECT sku FROM lake.silver.stock WHERE sku <> '\U0001f469‍\U0001f4bb' ORDER BY sku"},
    )
    assert res.status_code == 200, res.text
    assert [r["sku"] for r in res.json()["rows"]] == ["SKU-A", "SKU-B"]


def test_must_fail_pack_sql_check_refuses_a_hidden_table() -> None:
    """The insights pack path checks before the frozen crew FreeRoute validator."""
    from CortexOS.crew import cot_climb
    from CortexOS.crew import freeroute as crew_freeroute

    sql = f"SELECT * FROM inventory AS i{ZW}CROSS{ZW}JOIN{ZW}secrets"
    out = cot_climb._check_sql(crew_freeroute, sql, {}, {"inventory"}, {})
    assert out["ok"] is False and out["sql"] is None
    assert out["reason"].startswith("SQL carries U+200B")
    # Without the check, the frozen validator passes it as one table.
    assert crew_freeroute.validate_sql(sql, {"inventory"})["ok"] is True


def test_duckdb_reads_the_lake_hidden_table_twice() -> None:
    """The lake case is a real cross join in DuckDB: two rows become four."""
    db = duckdb.connect()
    db.execute("CREATE TABLE stock(sku VARCHAR); INSERT INTO stock VALUES ('SKU-A'), ('SKU-B')")
    assert len(db.execute(f"SELECT s.sku FROM stock AS s{ZW}CROSS{ZW}JOIN{ZW}stock").fetchall()) == 4
