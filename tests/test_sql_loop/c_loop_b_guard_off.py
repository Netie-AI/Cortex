"""pytest plugin: remove one C-LOOP-B guard, named by ``C_LOOP_B_GUARD_OFF``.

Loaded only by test_c_loop_b_guard_mutations.py in a subprocess, to prove each
must-fail test fails without the guard it protects.
"""

from __future__ import annotations

import os
from typing import Any


def pytest_configure(config: Any) -> None:
    from cortex_contract import answer

    from CortexOS.dms import sql_self_correct
    from CortexOS.dms.sql_validate_gate import ValidateGateResult

    guard = os.environ.get("C_LOOP_B_GUARD_OFF", "")
    if guard == "result_checks":
        sql_self_correct._failed_check = lambda *a, **k: None  # type: ignore[assignment]
    elif guard == "abstained_rows":
        answer._require_no_abstained_rows = lambda a: None  # type: ignore[assignment]
    elif guard == "named_reason":
        answer._require_named_abstain = lambda loop: None  # type: ignore[assignment]
    elif guard == "loop_retry_cap":
        sql_self_correct.MAX_RETRIES = 5
    elif guard == "wire_retry_cap":
        answer._require_retry_cap = lambda loop: None  # type: ignore[assignment]
    elif guard == "sql_gate":
        sql_self_correct._gated = lambda gate, sql: ValidateGateResult(  # type: ignore[assignment]
            passed=True, safe_sql=sql, source_sql=sql, attempts=1
        )
    else:
        raise RuntimeError(f"unknown C_LOOP_B_GUARD_OFF={guard!r}")
