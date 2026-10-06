"""T2-FAILCLOSED (#263): the suite runs with explicit test API keys.

Loaded for the whole suite as a pytest plugin through
``[tool.pytest.ini_options] addopts`` in ``pyproject.toml``. It is not in
``tests/conftest.py`` because
``tests/test_crew/test_cot_climb.py::test_branch_does_not_dual_write_freeroute_layer``
freezes that file.

Every test gets a fixed, test-only viewer / steward / admin key set in
``DMS_API_KEYS`` (replacing whatever the shell exported, so a local run never
picks up real keys) and the in-repo DMS request authorizer on the engine auth
port. The suite pins ``PACK=ruma``, so without the registration an engine-gated
route would refuse with 503 depending on test order. Autouse fixtures run
before a test's own fixtures, so a test that sets or deletes ``DMS_API_KEYS``,
or swaps the authorizer, still wins.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

import pytest

TEST_VIEWER_KEY: Final[str] = "pytest-viewer-key"
TEST_STEWARD_KEY: Final[str] = "pytest-steward-key"
TEST_ADMIN_KEY: Final[str] = "pytest-admin-key"
TEST_API_KEYS: Final[str] = (
    f"viewer:{TEST_VIEWER_KEY};steward:{TEST_STEWARD_KEY};admin:{TEST_ADMIN_KEY}"
)


@pytest.fixture(autouse=True)
def configured_test_api_keys(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("DMS_API_KEYS", TEST_API_KEYS)
    return {"viewer": TEST_VIEWER_KEY, "steward": TEST_STEWARD_KEY, "admin": TEST_ADMIN_KEY}


@pytest.fixture(autouse=True)
def registered_test_authorizer() -> Iterator[None]:
    from CortexOS.security import auth_port
    from packs.dms.security.request_authorizer import register_request_authorizer

    previous = auth_port.registered_authorizer()
    register_request_authorizer()
    yield
    if previous is None:
        auth_port.clear_authorizer()
    else:
        auth_port.register_authorizer(previous)
