"""Same warehouse-manifest bind as ``tests/dms`` for pipeline answer tests."""

from __future__ import annotations

import pytest

from tests.dms.session_manifest import install_auto_bind, uninstall_auto_bind


@pytest.fixture(scope="module", autouse=True)
def _auto_bind_warehouse_manifest_module() -> None:
    install_auto_bind()
    yield
    uninstall_auto_bind()
