"""C7-min — EXPLAIN dry-run + bounded retry before execute.

Assembles existing sqlglot guardrail + DuckDB EXPLAIN. Does not weaken
manifest refusals. L2 generation hooks call this gate; without a model the
answer engine still abstains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from CortexOS.dms.sql_guardrail import GuardrailResult, validate_sql


@dataclass(slots=True)
class ValidateGateResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    safe_sql: str | None = None
    explain_ok: bool = False
    attempts: int = 0
    explain_error: str | None = None


class SqlGateAbstain(Exception):
    """Raised when validation/EXPLAIN retries are exhausted — caller must abstain."""

    def __init__(self, message: str, *, violations: list[str] | None = None) -> None:
        super().__init__(message)
        self.violations = list(violations or [])


def explain_dry_run(con: Any, sql: str) -> tuple[bool, str]:
    """Run EXPLAIN without executing the query body. Returns (ok, detail)."""
    try:
        con.execute(f"EXPLAIN {sql}")
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:400]


def run_gate(
    sql: str,
    semantic: dict[str, Any],
    *,
    con: Any | None = None,
) -> ValidateGateResult:
    """sqlglot allowlist + optional EXPLAIN dry-run."""
    guard = validate_sql(sql, semantic)
    if not guard.passed or not guard.safe_sql:
        return ValidateGateResult(
            passed=False,
            violations=list(guard.violations),
            safe_sql=None,
            explain_ok=False,
            attempts=1,
        )

    if con is None:
        return ValidateGateResult(
            passed=True,
            violations=[],
            safe_sql=guard.safe_sql,
            explain_ok=True,  # no connection — parse-only pass
            attempts=1,
        )

    ok, detail = explain_dry_run(con, guard.safe_sql)
    if not ok:
        return ValidateGateResult(
            passed=False,
            violations=[f"EXPLAIN_FAILED:{detail}"],
            safe_sql=guard.safe_sql,
            explain_ok=False,
            attempts=1,
            explain_error=detail,
        )
    return ValidateGateResult(
        passed=True,
        violations=[],
        safe_sql=guard.safe_sql,
        explain_ok=True,
        attempts=1,
    )


def gate_with_retry(
    generate_fn: Callable[[list[str]], str | None],
    question: str,  # noqa: ARG001 — reserved for future schema-retrieval context
    semantic: dict[str, Any],
    *,
    con: Any | None = None,
    max_retries: int = 2,
) -> ValidateGateResult:
    """Call generate_fn up to max_retries+1 times, feeding prior violations back.

    ``generate_fn(prior_violations) -> sql | None``. Exhaustion raises SqlGateAbstain.
    """
    prior: list[str] = []
    last = ValidateGateResult(passed=False, attempts=0)
    for attempt in range(1, max_retries + 2):
        sql = generate_fn(prior)
        if not sql:
            last = ValidateGateResult(
                passed=False,
                violations=prior + ["NO_CANDIDATE"],
                attempts=attempt,
            )
            break
        last = run_gate(sql, semantic, con=con)
        last.attempts = attempt
        if last.passed and last.safe_sql:
            return last
        prior = list(last.violations)
    raise SqlGateAbstain(
        "SQL validation gate exhausted retries",
        violations=last.violations,
    )


def guard_result_from_gate(gate: ValidateGateResult) -> GuardrailResult:
    return GuardrailResult(
        passed=gate.passed,
        violations=list(gate.violations),
        safe_sql=gate.safe_sql,
    )


__all__ = [
    "SqlGateAbstain",
    "ValidateGateResult",
    "explain_dry_run",
    "gate_with_retry",
    "guard_result_from_gate",
    "run_gate",
]
