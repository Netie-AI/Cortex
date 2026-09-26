"""Grant keys match the table they name exactly (shared naming rule).

A table crosses the DMS -> Cortex boundary as ``schema.table`` (or bare for the
demo Space), and the manifest grant key is that same name. The enforcer used to
compare ``table.name`` only, so a qualified key could never match and a bare
key granted the same table name in every schema. This narrows the check; it
never widens it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
        session_id="sess-q",
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


QUALIFIED = _verified({"bronze.schools": "TRUE", "bronze.satscores": "county = 'A'"})
BARE = _verified({"orders": "tenant_id = 'a'"})


def test_qualified_key_grants_the_qualified_table() -> None:
    out = enforce_manifest(
        "SELECT s.cdscode, t.avgscrread FROM bronze.schools AS s "
        "JOIN bronze.satscores AS t ON t.cds = s.cdscode",
        QUALIFIED,
    )
    assert "bronze.satscores" in out
    assert "county = 'A'" in out


def test_qualified_key_predicate_applies_to_the_qualified_reference() -> None:
    out = enforce_manifest("SELECT count(*) FROM bronze.satscores", QUALIFIED)
    assert "WHERE county = 'A'" in out
    assert "AS satscores" in out


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM bronze.frpm",  # same schema, table not granted
        "SELECT * FROM silver.schools",  # granted bare name, other schema
        "SELECT * FROM main.schools",
        "SELECT * FROM schools",  # bare is not bronze.schools
    ],
)
def test_qualified_key_refuses_everything_it_does_not_name(sql: str) -> None:
    with pytest.raises(PathNotAllowed):
        enforce_manifest(sql, QUALIFIED)


def test_bare_key_still_grants_default_schema() -> None:
    assert "tenant_id = 'a'" in enforce_manifest("SELECT id FROM orders", BARE)
    assert "tenant_id = 'a'" in enforce_manifest("SELECT id FROM main.orders", BARE)


def test_bare_key_does_not_grant_other_schemas() -> None:
    with pytest.raises(PathNotAllowed):
        enforce_manifest("SELECT id FROM tenant_b.orders", BARE)


def test_cte_named_like_granted_bare_part_is_refused() -> None:
    """``schools`` is bound locally while ``bronze.schools`` is governed."""
    with pytest.raises(PathNotAllowed):
        enforce_manifest(
            "WITH schools AS (SELECT 1 AS x) SELECT * FROM schools, bronze.schools",
            QUALIFIED,
        )
