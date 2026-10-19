"""Install the declared-wording metric double for the whole suite.

Production registers the OpenVault port from the pack. Tests must not call
that port: it would spend a vault key or steal a scripted L2 reply. A
session fixture installs the double before any module fixture scores the
corpus. A function fixture reinstalls it after a test that swapped the port.
"""

from __future__ import annotations

import pytest


def _install() -> None:
    from CortexOS.dms.metric_choice import register_metric_choice
    from tests.dms.metric_choice_double import ExactDeclaredChoice

    register_metric_choice(ExactDeclaredChoice())


@pytest.fixture(scope="session", autouse=True)
def metric_choice_session_port() -> None:
    _install()
    yield
    from CortexOS.dms.metric_choice import clear_metric_choice

    clear_metric_choice()


@pytest.fixture(autouse=True)
def metric_choice_test_port() -> None:
    _install()
    yield
    _install()
