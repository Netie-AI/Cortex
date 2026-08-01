"""C7-min — EXPLAIN dry-run + bounded retry before execute.

Assembles existing sqlglot guardrail + DuckDB EXPLAIN. Does not weaken
manifest refusals. L2 generation hooks call this gate; without a model the
answer engine still abstains.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from CortexOS.dms.sql_guardrail import GuardrailResult, validate_sql


@dataclass(slots=True)
class ValidateGateResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    safe_sql: str | None = None
    explain_ok: bool = False
    attempts: int = 0
    explain_error: str | None = None
    #: Whether EXPLAIN was actually executed. ``explain_ok`` is only meaningful
    #: when this is True — see :func:`run_gate` on why the two are separate.
    explain_ran: bool = False
    explain_skipped_reason: str | None = None


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
    require_explain: bool = False,
) -> ValidateGateResult:
    """sqlglot allowlist + EXPLAIN dry-run when a connection is available.

    ``explain_ok`` used to be set True when no connection was passed, on the
    reasoning that a parse-only pass is still a pass. It reads as "EXPLAIN
    approved this SQL" to every caller downstream, which is a claim about a check
    that never ran — the shape of failure R-0011 is about. The result now carries
    ``explain_ran`` alongside, and a skipped EXPLAIN says so.

    ``require_explain`` turns the skip into a refusal. Callers set it for SQL a
    model wrote, where the dry-run is the only thing standing between a
    hallucinated column and the customer; callers running semantic-layer SQL that
    a later stage will EXPLAIN anyway leave it False rather than fail an answer
    that is in fact still gated.
    """
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
        reason = "no warehouse connection — EXPLAIN not run"
        if require_explain:
            return ValidateGateResult(
                passed=False,
                violations=[f"EXPLAIN_UNAVAILABLE:{reason}"],
                safe_sql=guard.safe_sql,
                explain_ok=False,
                attempts=1,
                explain_ran=False,
                explain_skipped_reason=reason,
            )
        return ValidateGateResult(
            passed=True,
            violations=[],
            safe_sql=guard.safe_sql,
            explain_ok=False,
            attempts=1,
            explain_ran=False,
            explain_skipped_reason=reason,
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
            explain_ran=True,
        )
    return ValidateGateResult(
        passed=True,
        violations=[],
        safe_sql=guard.safe_sql,
        explain_ok=True,
        attempts=1,
        explain_ran=True,
    )


def gate_with_retry(
    generate_fn: Callable[[list[str]], str | list[str] | None],
    question: str,  # noqa: ARG001 — reserved for future schema-retrieval context
    semantic: dict[str, Any],
    *,
    con: Any | None = None,
    max_retries: int = 2,
    require_explain: bool = False,
) -> ValidateGateResult:
    """Call generate_fn up to max_retries+1 times, feeding prior violations back.

    ``generate_fn(prior_violations) -> sql | [sql, …] | None``. A provider that
    returns several candidates gets all of them gated before the round is spent:
    discarding candidates 2..n and paying for another round-trip to regenerate
    was throwing away work that had already been done, and the retry budget is
    the thing that decides whether the answer path abstains.

    Violations from every candidate in a round are fed back, so the next prompt
    sees each way the round failed rather than only the last one. Exhaustion
    raises :class:`SqlGateAbstain` — the caller abstains, never falls back.
    """
    prior: list[str] = []
    last = ValidateGateResult(passed=False, attempts=0)
    for attempt in range(1, max_retries + 2):
        produced = generate_fn(prior)
        candidates = (
            [produced]
            if isinstance(produced, str)
            else [c for c in (produced or []) if isinstance(c, str) and c.strip()]
        )
        if not candidates:
            last = ValidateGateResult(
                passed=False,
                violations=prior + ["NO_CANDIDATE"],
                attempts=attempt,
            )
            break
        round_violations: list[str] = []
        for sql in candidates:
            last = run_gate(sql, semantic, con=con, require_explain=require_explain)
            last.attempts = attempt
            if last.passed and last.safe_sql:
                return last
            round_violations.extend(last.violations)
        # Deduplicate while preserving order — a model reading the same violation
        # three times learns nothing extra and it eats the prompt budget.
        seen: set[str] = set()
        prior = [v for v in round_violations if not (v in seen or seen.add(v))]
        last.violations = list(prior)
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
