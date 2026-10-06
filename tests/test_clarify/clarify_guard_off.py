"""pytest plugin: remove one CLARIFY guard, named by ``CLARIFY_GUARD_OFF``.

Loaded only by test_clarify_guard_mutations.py in a subprocess, to prove each
must-fail test fails without the guard it protects.
"""

from __future__ import annotations

import os
from typing import Any


def pytest_configure(config: Any) -> None:
    import cortex_contract.answer as contract

    from CortexOS.clarify import core
    from CortexOS.dms import clarify_ask

    guard = os.environ.get("CLARIFY_GUARD_OFF", "")
    if guard == "ambiguity_detect":
        core._time_window = lambda *a, **k: None  # type: ignore[assignment]
        core._metric = lambda *a, **k: None  # type: ignore[assignment]
        core._term_column = lambda *a, **k: None  # type: ignore[assignment]
    elif guard == "ask_gate":
        clarify_ask.try_clarify = lambda *a, **k: None  # type: ignore[assignment]
    elif guard == "clarify_shape":
        core.ensure_valid = lambda clarify: clarify  # type: ignore[assignment]
        contract._require_options_and_type = lambda clarify: None  # type: ignore[assignment]
        contract._require_no_numbers_on_clarify = lambda answer: None  # type: ignore[assignment]
    elif guard == "unambiguous_anchor":
        core._settled = lambda routed: False  # type: ignore[assignment]
        core._anchored = lambda *a, **k: False  # type: ignore[assignment]
    else:
        raise RuntimeError(f"unknown CLARIFY_GUARD_OFF={guard!r}")
