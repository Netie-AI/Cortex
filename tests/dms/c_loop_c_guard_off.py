"""pytest plugin: remove one C-LOOP-C guard, named by ``C_LOOP_C_GUARD_OFF``.

Loaded only by test_c_loop_c_guard_mutations.py in a subprocess, to prove each
must-fail test fails without the guard it protects.
"""

from __future__ import annotations

import os
from typing import Any


def pytest_configure(config: Any) -> None:
    from CortexOS.dms import result_package as rp

    guard = os.environ.get("C_LOOP_C_GUARD_OFF", "")
    if guard == "insight_numbers":
        rp.untraceable_numbers = lambda text, rows: []  # type: ignore[assignment]
    elif guard == "chart_fields":
        rp.unknown_chart_fields = lambda spec, columns: []  # type: ignore[assignment]
    elif guard == "abstain":
        rp.is_abstain = lambda answer: False  # type: ignore[assignment]
    else:
        raise RuntimeError(f"unknown C_LOOP_C_GUARD_OFF={guard!r}")
