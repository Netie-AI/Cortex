"""pytest plugin: serve the model-chosen metric without the meaning check.

Loaded only by test_metric_exactness_guard_mutations.py, to prove the #412
must-fails go red without the check.
"""

from __future__ import annotations

from typing import Any


def pytest_configure(config: Any) -> None:
    del config
    from CortexOS.dms import metric_choice

    metric_choice.metric_check_enabled = lambda: False  # type: ignore[method-assign]
