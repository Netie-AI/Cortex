"""pytest plugin: remove one C-LOOP-A guard, named by ``PLAN_SQL_GUARD_OFF``.

Loaded only by test_c_loop_a_guard_mutations.py in a subprocess, to prove each
must-fail test fails without the guard it protects.
"""

from __future__ import annotations

import os
from typing import Any


def pytest_configure(config: Any) -> None:
    from CortexOS.dms import plan_sql_ask
    from CortexOS.dms.sql_validate_gate import ValidateGateResult
    from CortexOS.plan_sql import payload

    guard = os.environ.get("PLAN_SQL_GUARD_OFF", "")
    if guard == "freeroute_only":
        plan_sql_ask._journaled = lambda step, journal: True  # type: ignore[assignment]
    elif guard == "sql_gate":
        plan_sql_ask._gate = lambda sql, grant: ValidateGateResult(  # type: ignore[assignment]
            passed=True, safe_sql=sql, source_sql=sql
        )
    elif guard == "payload_grant":
        payload.widening = lambda request, granted: []  # type: ignore[assignment]
    elif guard == "payload_scope":
        plan_sql_ask._sql_tables = lambda sql: set()  # type: ignore[assignment]
    else:
        raise RuntimeError(f"unknown PLAN_SQL_GUARD_OFF={guard!r}")
