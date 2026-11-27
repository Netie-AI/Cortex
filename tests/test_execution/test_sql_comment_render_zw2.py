"""JOIN-GRANT-ZW part 2: a comment in served SQL changed a literal's value.

DuckDB strips Unicode spaces outside quotes before it parses, and the quote
tracking of that pass does not skip block comments. sqlglot renders every
comment as ``/* ... */``, so an apostrophe in a model's ``-- customer's`` note
flips the pass into "inside a quote" and back out at the next literal: the
NBSP inside ``'a<NBSP>b'`` is rewritten to a plain space, and DuckDB returns
the ``'a b'`` row. A wrong row served (WRONG=0). Found by the #408 verifier
round.

The SQL gates now render the SQL they serve without comments. A comment carries
no meaning, so no answer changes; the corpus and the suite run on it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import duckdb
import pytest
import sqlglot

from CortexOS.dms.sql_guardrail import guard_and_execute, validate_sql
from CortexOS.execution.manifest import Manifest, VerifiedManifest, enforce_manifest
from CortexOS.insights import caller_sql

NBSP = " "
RIGHT = [(f"a{NBSP}b",)]
WRONG = [("a b",)]
#: Each asks for the NBSP row; a comment holding an apostrophe sits before it.
CASES = {
    "line_comment": f"SELECT v FROM t -- customer's filter\nWHERE v = 'a{NBSP}b'",
    "block_comment": f"SELECT v FROM t /* the customer's filter */ WHERE v = 'a{NBSP}b'",
    "comment_in_select": f"SELECT v /* it's v */ FROM t WHERE v = 'a{NBSP}b'",
    "comment_after_literal_twice": (
        f"SELECT v FROM t WHERE v = 'zz' -- don't\nOR v = 'a{NBSP}b' -- won't\n"
    ),
}


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t (v VARCHAR)")
    con.execute("INSERT INTO t VALUES ('a b'), (?)", [f"a{NBSP}b"])
    return con


@pytest.fixture(scope="module")
def verified() -> VerifiedManifest:
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="sess-zw2",
        org_id="acme",
        pool_id="pool-a",
        issuer_key_id="int-1",
        allowed_paths=[],
        row_predicates={"t": "TRUE"},
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="int-1", verified_at=now)


@pytest.mark.parametrize("name", list(CASES))
def test_the_commented_render_returns_the_wrong_row(name: str) -> None:
    """The hazard: the statement rendered by sqlglot with its comment returns the wrong row,
    and rendered without it returns the right one."""
    tree = sqlglot.parse_one(CASES[name], read="duckdb")
    con = _con()
    with_comment = tree.sql(dialect="duckdb")
    assert "/*" in with_comment
    assert con.execute(with_comment).fetchall() == WRONG
    assert con.execute(tree.sql(dialect="duckdb", comments=False)).fetchall() == RIGHT


@pytest.mark.parametrize("name", list(CASES))
def test_must_fail_manifest_serves_the_right_row(name: str, verified: VerifiedManifest) -> None:
    served = enforce_manifest(CASES[name], verified)
    assert "/*" not in served and "--" not in served
    assert _con().execute(served).fetchall() == RIGHT


@pytest.mark.parametrize("name", list(CASES))
def test_must_fail_caller_gate_serves_the_right_row(name: str) -> None:
    out = caller_sql.validate_caller_sql(CASES[name], {"t": ["v"]})
    assert out["ok"] is True, out
    assert "/*" not in out["sql"]
    assert _con().execute(out["sql"]).fetchall() == RIGHT


@pytest.mark.parametrize("name", list(CASES))
def test_must_fail_guardrail_serves_the_right_row(name: str) -> None:
    semantic = {"tables": {"t": {"columns": ["v"]}}}
    result, rows, _audit = guard_and_execute(CASES[name], semantic, _con())
    assert result.passed is True, result.violations
    assert "/*" not in (result.safe_sql or "")
    assert [tuple(r.values()) for r in rows] == RIGHT


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT v FROM t -- all rows\nORDER BY v",
        "SELECT /* count */ COUNT(*) AS n FROM t",
        "SELECT v FROM t WHERE v = 'a b' /* plain space */",
        "SELECT v FROM t WHERE v LIKE '%--%' OR v = 'a b'",
        "SELECT v FROM t WHERE v = '/* not a comment */' OR v = 'a b'",
    ],
)
def test_must_pass_dropping_comments_changes_no_answer(sql: str, verified: VerifiedManifest) -> None:
    con = _con()
    expected = con.execute(sql).fetchall()
    assert sorted(con.execute(enforce_manifest(sql, verified)).fetchall()) == sorted(expected)
    gate = caller_sql.validate_caller_sql(sql, {"t": ["v"]})["sql"]
    assert sorted(con.execute(gate).fetchall()) == sorted(expected)
    guard = validate_sql(sql, {"tables": {"t": {"columns": ["v"]}}}).safe_sql
    assert guard is not None and sorted(con.execute(guard).fetchall()) == sorted(expected)
