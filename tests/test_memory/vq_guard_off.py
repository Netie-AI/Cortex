"""pytest plugin: remove one VERIFIED-QUERY guard, named by ``VQ_GUARD_OFF``.

Loaded only by test_verified_query_guard_mutations.py in a subprocess, to prove
each must-fail test fails without the guard it protects.
"""

from __future__ import annotations

import os
from typing import Any


def pytest_configure(config: Any) -> None:
    from CortexOS.memory import verified_query as vq
    from CortexOS.memory.space_memory import Actor, SpaceMemory

    guard = os.environ.get("VQ_GUARD_OFF", "")
    lib = vq.VerifiedQueryLibrary
    if guard == "confirm_gate":
        # Both confirm gates: a proposal lands in solution memory, and any entry is live.
        propose = lib.propose

        def leaky_propose(self: Any, **kw: Any) -> Any:
            query = propose(self, **kw)
            self.memory.confirm_solution(
                space_id=query.space_id,
                question=query.question,
                sql=query.sql,
                steward=Actor(kw["actor"], "steward"),
                source=kw["source"],
            )
            return query

        lib.propose = leaky_propose  # type: ignore[method-assign]
        lib._live = staticmethod(lambda query, entry: entry is not None)  # type: ignore[method-assign]
    elif guard == "revoke":
        lib._retire = lambda self, query, steward: ()  # type: ignore[method-assign]
    elif guard == "scored_pack":
        SpaceMemory._refuse_scored_pack = lambda self, **kw: None  # type: ignore[method-assign]
        SpaceMemory.is_scored_round = lambda self, scored_pack_id=None: False  # type: ignore[method-assign]
    elif guard == "space_isolation":
        SpaceMemory._in_space = staticmethod(lambda space_id: ("1 = 1", []))  # type: ignore[method-assign]
        lib._in_space = staticmethod(lambda space_id: ("1 = 1", []))  # type: ignore[method-assign]
    elif guard == "param_validation":
        vq.validate_value = lambda kind, value: value
    elif guard == "param_binding":

        def interpolate(sql: str, params: Any, values: Any) -> tuple[str, tuple[Any, ...]]:
            for p, v in zip(params, values, strict=True):
                sql = sql.replace(f"'{p.literal}'", f"'{v}'")
            return sql, ()

        vq.bind_sql = interpolate
    elif guard == "grant_gate":
        from CortexOS.execution import submit

        submit.enforce_manifest = lambda sql, verified: sql
    else:
        raise RuntimeError(f"unknown VQ_GUARD_OFF={guard!r}")
