"""T2-FAILCLOSED (#263): the suite runs with explicit test API keys.

Loaded for the whole suite as a pytest plugin through
``[tool.pytest.ini_options] addopts`` in ``pyproject.toml``, next to
``tests.runtime_log_isolation``. It lives here rather than in
``tests/conftest.py`` because that file belongs to the #215 FreeRoute layer and
``tests/test_crew/test_cot_climb.py::test_branch_does_not_dual_write_freeroute_layer``
forbids any branch diff to it.

``packs/dms/security/api_auth.py`` no longer has a built-in key: with
``DMS_API_KEYS`` unset every gated request is refused. Tests that exercise a
gated route as an ordinary caller therefore need keys configured, and this
fixture configures a fixed, test-only set for every test. The values are not
secrets and are accepted nowhere but inside a test process that set them.

Autouse fixtures run before a test's non-autouse fixtures and its body, so a
test that sets ``DMS_API_KEYS`` itself, or deletes it with
``monkeypatch.delenv``, still wins. The fixture also replaces whatever the
developer's shell exported, so a local run cannot pick up real keys.
"""

from __future__ import annotations

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
    """Configure the test-only viewer / steward / admin keys for this test."""
    monkeypatch.setenv("DMS_API_KEYS", TEST_API_KEYS)
    return {"viewer": TEST_VIEWER_KEY, "steward": TEST_STEWARD_KEY, "admin": TEST_ADMIN_KEY}
