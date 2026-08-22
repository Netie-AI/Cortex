"""Test-only warehouse VerifiedManifest. Does not mint a production grant."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from CortexOS.dms.warehouse_db import KNOWN_TABLES
from CortexOS.execution.manifest import VerifiedManifest
from CortexOS.execution.session_manifests import get_session_registry
from packages.cortex_contract.execution import Manifest

WAREHOUSE_TABLES: tuple[str, ...] = tuple(KNOWN_TABLES)


def warehouse_verified(
    session_id: str,
    *,
    tables: tuple[str, ...] | None = None,
) -> VerifiedManifest:
    """Construct a VerifiedManifest covering warehouse tables (tests only).

    Signature is not checked here — same pattern as the C3 enforcer corpus.
    Production /dms/query never builds this; it only resolves a prior bind.
    """
    now = datetime.now(timezone.utc)
    granted = tables if tables is not None else WAREHOUSE_TABLES
    manifest = Manifest(
        session_id=session_id,
        org_id="demo",
        pool_id="default",
        issuer_key_id="test-int",
        allowed_paths=["/data/pool/demo/**"],
        row_predicates={name: "TRUE" for name in granted},
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        signature="not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="test-int", verified_at=now)


def bind_warehouse_session(
    session_id: str = "demo",
    *,
    tables: tuple[str, ...] | None = None,
) -> VerifiedManifest:
    verified = warehouse_verified(session_id, tables=tables)
    get_session_registry().bind(verified)
    return verified
