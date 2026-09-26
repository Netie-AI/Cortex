"""A CTE exempts a table reference from the grant check only where scope binds it.

Root-cause class: every CTE bypass so far was a hand-rolled "is this name a
CTE" check without scope -- CTE names collected tree-wide, then every bare
table of that name exempted. DuckDB binds names by scope, so each case below
reads the *real* ``secrets`` table (proven on DuckDB first), and the manifest
must therefore refuse it: ``secrets`` is not granted.

Also: a quoted identifier whose single part contains a dot
(``main."bronze.schools"``) is one relation, not ``schema.table``, and must
never match the grant key ``bronze.schools``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import duckdb
import pytest
from cortex_contract.execution import Manifest

from CortexOS.execution.manifest import (
    PathNotAllowed,
    VerifiedManifest,
    enforce_manifest,
)


def _verified(predicates: dict[str, str]) -> VerifiedManifest:
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="sess-cte",
        org_id="acme",
        pool_id="pool-a",
        issuer_key_id="int-1",
        allowed_paths=[],
        row_predicates=predicates,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="int-1", verified_at=now)


GRANT = _verified({"orders": "tenant_id = 'a'", "bronze.schools": "TRUE"})

SECRET = 424242

SCOPE_ESCAPES = {
    "cte_in_derived_table_shadows_outer_real_table": (
        "SELECT s.id FROM (WITH secrets AS (SELECT 1 AS id) SELECT id FROM secrets) AS d, "
        "secrets AS s"
    ),
    "forward_reference_to_later_cte": (
        "WITH a AS (SELECT id FROM secrets), secrets AS (SELECT 1 AS id) SELECT id FROM a"
    ),
    "same_named_cte_and_real_table_in_different_scopes": (
        "SELECT (SELECT max(id) FROM secrets) AS id "
        "FROM (WITH secrets AS (SELECT 1 AS id) SELECT id FROM secrets) AS d"
    ),
    "recursive_cte_anchor_arm_reads_real_table": (
        "WITH RECURSIVE secrets AS (SELECT id FROM secrets UNION ALL "
        "SELECT id + 1 FROM secrets WHERE id < 0) SELECT id FROM secrets"
    ),
}


def _lake() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE secrets (id INTEGER)")
    con.execute(f"INSERT INTO secrets VALUES ({SECRET})")
    con.execute("CREATE TABLE orders (id INTEGER, tenant_id VARCHAR)")
    con.execute("INSERT INTO orders VALUES (1, 'a'), (2, 'b')")
    return con


@pytest.mark.parametrize("sql", list(SCOPE_ESCAPES.values()), ids=list(SCOPE_ESCAPES))
def test_duckdb_binds_the_name_to_the_real_table(sql: str) -> None:
    """The premise: each query really does return the ungranted table's rows."""
    assert (SECRET,) in _lake().execute(sql).fetchall()


@pytest.mark.parametrize("sql", list(SCOPE_ESCAPES.values()), ids=list(SCOPE_ESCAPES))
def test_cte_outside_its_scope_does_not_exempt_the_real_table(sql: str) -> None:
    with pytest.raises(PathNotAllowed) as caught:
        enforce_manifest(sql, GRANT)
    assert "secrets" in str(caught.value)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH c AS (SELECT id FROM orders) SELECT a.id FROM c AS a, (SELECT id FROM c) AS b",
        "WITH x AS (SELECT id FROM orders) SELECT id FROM (WITH y AS (SELECT id FROM x) "
        "SELECT id FROM y) AS d",
        "WITH RECURSIVE chain AS (SELECT id FROM orders UNION ALL "
        "SELECT id + 1 FROM chain WHERE id < 3) SELECT id FROM chain",
        "SELECT (WITH c AS (SELECT id FROM orders) SELECT max(id) FROM c) AS m",
    ],
    ids=["cte_in_derived_table", "nested_with", "recursive_arm", "cte_in_scalar_subquery"],
)
def test_ctes_bound_by_scope_still_work_and_carry_the_predicate(sql: str) -> None:
    out = enforce_manifest(sql, GRANT)
    assert "tenant_id = 'a'" in out
    rows = _lake().execute(out).fetchall()
    assert rows and all(SECRET not in row for row in rows)


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT * FROM main."bronze.schools"',
        'SELECT * FROM "bronze.schools"',
        'SELECT * FROM "bronze.schools" AS s',
        'SELECT * FROM bronze."schools.x"',
    ],
    ids=["main_dotted", "bare_dotted", "aliased_dotted", "dotted_table_part"],
)
def test_dotted_identifier_part_never_matches_a_qualified_grant(sql: str) -> None:
    with pytest.raises(PathNotAllowed) as caught:
        enforce_manifest(sql, GRANT)
    assert "contains a dot" in str(caught.value)


def test_quoted_parts_still_match_as_parsed_pairs() -> None:
    out = enforce_manifest('SELECT * FROM "bronze"."schools"', GRANT)
    assert "WHERE TRUE" in out

