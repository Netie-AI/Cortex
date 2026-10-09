"""pytest plugin: remove one C-LOOP guard, named by ``C_LOOP_GUARD_OFF``.

Loaded only by test_c_loop_guard_mutations.py in a subprocess, to prove each
must-fail test fails without the guard it protects.
"""

from __future__ import annotations

import os
from typing import Any


def pytest_configure(config: Any) -> None:
    guard = os.environ.get("C_LOOP_GUARD_OFF", "")
    if guard == "evaluate":
        from CortexOS.loop.orchestrator import _Round

        _Round.evaluate = lambda self, cand, rows: None  # type: ignore[method-assign]
    elif guard == "check":
        from CortexOS.loop.orchestrator import _Round

        _Round.check = lambda self, cand, rows: None  # type: ignore[method-assign]
    elif guard == "named_reason":
        from cortex_contract.answer import AnalysisLoop, Answer

        from CortexOS.loop import orchestrator

        AnalysisLoop.__pydantic_decorators__.model_validators.pop("_abstain_is_named")
        AnalysisLoop.model_rebuild(force=True)
        Answer.model_rebuild(force=True)
        orchestrator.named = lambda reason: getattr(reason, "value", reason)  # type: ignore[assignment]
    elif guard == "scored_pack":
        from CortexOS.memory.space_memory import SpaceMemory

        SpaceMemory._refuse_scored_pack = lambda self, **kw: None  # type: ignore[method-assign]
        SpaceMemory.is_scored_round = lambda self, scored_pack_id=None: False  # type: ignore[method-assign]
    elif guard == "sandbox":
        from CortexOS.loop import sandbox, tools

        sandbox._check = lambda run, event, args: None  # type: ignore[assignment]
        tools.plain_data = lambda value, where="inputs": value  # type: ignore[assignment]
    else:
        raise RuntimeError(f"unknown C_LOOP_GUARD_OFF={guard!r}")
