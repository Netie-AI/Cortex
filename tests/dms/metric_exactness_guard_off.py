"""pytest plugin: serve keyword-cascade plans even when they are not exact.

Loaded only by test_metric_exactness_guard_mutations.py, to prove the #412
must-fails go red without the guard.
"""

from __future__ import annotations

from typing import Any


def pytest_configure(config: Any) -> None:
    del config
    from CortexOS.dms import answer_engine

    answer_engine._exactness_guard_enabled = lambda: False  # type: ignore[method-assign]
