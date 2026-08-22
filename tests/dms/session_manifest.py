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


def install_auto_bind() -> None:
    """Patch resolve so tests that never bind still hit enforce_manifest.

    Not a production grant. Tests that need a truly unbound session should
    call :func:`uninstall_auto_bind` first.
    """
    from CortexOS.execution.session_manifests import SessionUnbound

    registry = get_session_registry()
    if getattr(registry, "_cortex6_auto_bind", False):
        return
    original = registry.resolve

    def resolve(session_id, *, now=None):
        try:
            return original(session_id, now=now)
        except SessionUnbound:
            sid = (session_id or "demo").strip() or "demo"
            bind_warehouse_session(sid)
            return original(sid, now=now)

    registry.resolve = resolve  # type: ignore[method-assign]
    registry._cortex6_auto_bind = True  # type: ignore[attr-defined]
    registry._cortex6_resolve_original = original  # type: ignore[attr-defined]
    bind_warehouse_session("demo")


def uninstall_auto_bind() -> None:
    from CortexOS.execution.session_manifests import (
        SessionManifestRegistry,
        reset_session_registry_for_tests,
    )

    registry = get_session_registry()
    original = getattr(registry, "_cortex6_resolve_original", None)
    if original is not None:
        registry.resolve = original  # type: ignore[method-assign]
    else:
        registry.resolve = SessionManifestRegistry.resolve.__get__(
            registry, SessionManifestRegistry
        )
    registry._cortex6_auto_bind = False  # type: ignore[attr-defined]
    reset_session_registry_for_tests()
