"""Fail auth assertions when CORTEX_DEV_MODE is set.

Loaded from the repo-root conftest. tests/conftest.py stays untouched: the
FreeRoute dual-write guard treats that file as owned by another lane.

Covers tests/**/test_run_spend_auth* (the #362 flag table lives in that
module), the constructor image auth test, and constructor-graph auth tests.
"""

from __future__ import annotations

import os
import re

import pytest

_DEV_MODE_TRUTHY = frozenset({"1", "true", "yes"})
_CONSTRUCTOR_AUTH_NAME = re.compile(
    r"auth|key|login|session|viewer|401|cookie|ov_token|demo_keys",
    re.IGNORECASE,
)


def _dev_mode_set() -> bool:
    return os.environ.get("CORTEX_DEV_MODE", "").lower() in _DEV_MODE_TRUTHY


def _is_auth_assertion(item: pytest.Item) -> bool:
    nodeid = item.nodeid.replace("\\", "/")
    if "test_run_spend_auth" in nodeid:
        return True
    if (
        "test_constructor_hyperlift_dockerfile.py::" in nodeid
        and "auth" in item.name.lower()
    ):
        return True
    if "test_constructor_graph.py::" in nodeid:
        return _CONSTRUCTOR_AUTH_NAME.search(item.name) is not None
    return False


def _auth_test_in_dev_mode(item: pytest.Item) -> str | None:
    if _is_auth_assertion(item) and _dev_mode_set():
        return (
            "AUTH_TEST_IN_DEV_MODE: "
            f"{item.nodeid} ran while CORTEX_DEV_MODE is set"
        )
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item: pytest.Item):
    """Fail after fixtures if an auth assertion is already in dev mode."""
    outcome = yield
    message = _auth_test_in_dev_mode(item)
    if message is not None and not outcome.excinfo:
        outcome.force_exception(pytest.fail.Exception(message))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item):
    """Fail if dev mode is on before the body or was switched on during it.

    The second check runs before fixture teardown, so a setenv inside the
    test is still visible.
    """
    before = _auth_test_in_dev_mode(item)
    outcome = yield
    message = before or _auth_test_in_dev_mode(item)
    if message is not None:
        outcome.force_exception(pytest.fail.Exception(message))
