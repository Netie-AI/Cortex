"""Bind a warehouse VerifiedManifest for DMS answer tests (module scope).

Module-scoped benches call ``answer_question`` from a module fixture, which
runs before function-scoped autouse. Bind here so those still go through
``enforce_manifest`` instead of refusing as unbound.
"""

from __future__ import annotations

import pytest

from tests.dms.session_manifest import install_auto_bind, uninstall_auto_bind


@pytest.fixture(scope="module", autouse=True)
def _auto_bind_warehouse_manifest_module() -> None:
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()
    install_auto_bind()
    yield
    uninstall_auto_bind()
