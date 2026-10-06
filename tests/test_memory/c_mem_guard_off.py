"""pytest plugin: remove one C-MEM guard, named by ``C_MEM_GUARD_OFF``.

Loaded only by test_c_mem_guard_mutations.py in a subprocess, to prove each
must-fail test fails without the guard it protects.
"""

from __future__ import annotations

import os
from typing import Any


def pytest_configure(config: Any) -> None:
    from CortexOS.memory.space_memory import SpaceMemory

    guard = os.environ.get("C_MEM_GUARD_OFF", "")
    if guard == "scored_pack":
        SpaceMemory._refuse_scored_pack = lambda self, **kw: None  # type: ignore[method-assign]
    elif guard == "space_isolation":
        SpaceMemory._in_space = staticmethod(lambda space_id: ("1 = 1", []))  # type: ignore[method-assign]
    elif guard == "reused_validation":
        SpaceMemory._refuse_reused = lambda self, **kw: None  # type: ignore[method-assign]
    else:
        raise RuntimeError(f"unknown C_MEM_GUARD_OFF={guard!r}")
