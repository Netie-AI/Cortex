"""pytest plugin: remove SCHEMA-RETRIEVE guards named by ``SCHEMA_RETRIEVE_GUARD_OFF``.

Comma-separated. Loaded only by test_schema_retrieve_guard_mutations.py in a
subprocess, to prove each must-fail test fails without the guard it protects.
``cmem_*`` names switch off the #290 store's own guards, reused here.
"""

from __future__ import annotations

import os
from typing import Any


def pytest_configure(config: Any) -> None:
    from CortexOS.memory.space_memory import SpaceMemory
    from CortexOS.schema_retrieve import core

    for guard in filter(None, os.environ.get("SCHEMA_RETRIEVE_GUARD_OFF", "").split(",")):
        if guard == "grant":
            core._admit = lambda catalog, grant: catalog  # type: ignore[assignment]
        elif guard == "space":
            core._in_space = lambda entries, space_id: list(entries)  # type: ignore[assignment]
        elif guard == "rerank_scope":
            core._within = lambda order, allowed: list(dict.fromkeys(order))  # type: ignore[assignment]
        elif guard == "cmem_space_isolation":
            SpaceMemory._in_space = staticmethod(lambda space_id: ("1 = 1", []))  # type: ignore[method-assign]
        elif guard == "cmem_scored_pack":
            SpaceMemory._refuse_scored_pack = lambda self, **kw: None  # type: ignore[method-assign]
        else:
            raise RuntimeError(f"unknown SCHEMA_RETRIEVE_GUARD_OFF={guard!r}")
