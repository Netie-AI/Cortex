"""P22 — Agent SDK warehouse reads must go through enforce_manifest.

Ungranted tables refuse. Granted tables return rows and the row predicate
is actually applied to those rows (not only to generated SQL).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cortex_contract.execution import Manifest

from CortexOS.agent_sdk import AgentActor, SdkDenied, query_objects
from CortexOS.agent_sdk.backends import get_query_backend
from CortexOS.execution.manifest import VerifiedManifest
from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
from CortexOS.execution.session_manifests import (
    SessionUnbound,
    get_session_registry,
    reset_session_registry_for_tests,
)

VIEWER = AgentActor(actor="sdk_manifest_viewer", role="viewer")
INVENTORY_ONLY = {"inventory": "location_id = 'LOC-001'"}


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from CortexOS.dms.warehouse_db import load_inventory_csv

    db = tmp_path_factory.mktemp("wh") / "wh.duckdb"
    load_inventory_csv(db_path=db)
    return db


@pytest.fixture()
def pool() -> None:
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    yield
    reset_read_pool_for_tests()


def _verified(
    predicates: dict[str, str],
    *,
    session_id: str = "sdk-sess",
    issuer_kid: str = "int-1",
) -> VerifiedManifest:
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id=session_id,
        org_id="acme",
        pool_id="default",
        issuer_key_id=issuer_kid,
        allowed_paths=["/data/pool/acme/**"],
        row_predicates=predicates,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid=issuer_kid, verified_at=now)


def test_sdk_read_refuses_without_session_or_manifest(warehouse: Path, pool: None) -> None:
    reset_session_registry_for_tests()
    with pytest.raises(SdkDenied) as caught:
        query_objects("inventory", actor=VIEWER, pack="dms", db_path=warehouse)
    assert caught.value.verdict == "session_unbound"


def test_sdk_read_of_ungranted_table_is_refused(warehouse: Path, pool: None) -> None:
    verified = _verified(INVENTORY_ONLY)
    with pytest.raises(SdkDenied) as caught:
        query_objects(
            "suppliers",
            actor=VIEWER,
            pack="dms",
            db_path=warehouse,
            verified=verified,
        )
    assert caught.value.verdict == "path_not_allowed"
    assert "suppliers" in str(caught.value).lower()


def test_sdk_read_of_granted_table_applies_predicate(warehouse: Path, pool: None) -> None:
    verified = _verified(INVENTORY_ONLY)
    rows = query_objects(
        "inventory",
        actor=VIEWER,
        pack="dms",
        limit=500,
        db_path=warehouse,
        verified=verified,
    )
    assert rows, "LOC-001 inventory must exist in the sample warehouse"
    assert all(r["location_id"] == "LOC-001" for r in rows)
    assert {r["location_id"] for r in rows} == {"LOC-001"}


def test_sdk_granted_read_keeps_bind_params_under_predicate(
    warehouse: Path, pool: None
) -> None:
    verified = _verified(INVENTORY_ONLY)
    rows = query_objects(
        "inventory",
        actor=VIEWER,
        pack="dms",
        limit=500,
        db_path=warehouse,
        verified=verified,
    )
    category = rows[0]["category"]
    filtered = query_objects(
        "inventory",
        {"category": category},
        actor=VIEWER,
        pack="dms",
        limit=500,
        db_path=warehouse,
        verified=verified,
    )
    assert filtered
    assert all(r["location_id"] == "LOC-001" and r["category"] == category for r in filtered)


def test_sdk_read_via_bound_session_id(warehouse: Path, pool: None) -> None:
    reset_session_registry_for_tests()
    verified = _verified(INVENTORY_ONLY, session_id="bound-sdk")
    get_session_registry().bind(verified)
    try:
        rows = query_objects(
            "inventory",
            actor=VIEWER,
            pack="dms",
            limit=50,
            db_path=warehouse,
            session_id="bound-sdk",
        )
        assert rows and all(r["location_id"] == "LOC-001" for r in rows)
        with pytest.raises(SdkDenied) as caught:
            query_objects(
                "suppliers",
                actor=VIEWER,
                pack="dms",
                db_path=warehouse,
                session_id="bound-sdk",
            )
        assert caught.value.verdict == "path_not_allowed"
    finally:
        reset_session_registry_for_tests()


def test_sdk_backend_refuses_missing_manifest_without_opening(warehouse: Path) -> None:
    backend = get_query_backend("dms")
    with pytest.raises(SessionUnbound):
        backend("inventory", ["sku"], {}, 3, verified=None, db_path=warehouse)


def test_sdk_self_issued_grant_is_refused(warehouse: Path, pool: None) -> None:
    verified = _verified(INVENTORY_ONLY, issuer_kid="local-self-issued")
    with pytest.raises(SdkDenied) as caught:
        query_objects(
            "inventory",
            actor=VIEWER,
            pack="dms",
            db_path=warehouse,
            verified=verified,
        )
    assert caught.value.verdict == "session_unbound"
    assert "self-issued" in str(caught.value)
