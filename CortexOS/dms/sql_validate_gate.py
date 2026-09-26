"""C7 — sqlglot + optional manifest enforce + EXPLAIN + bounded retry.

A grounded session runs ``enforce_manifest`` before EXPLAIN so the dry-run
never sees pre-enforce SQL (C7-02). Manifest refusals are not retried for
the same SQL. This module does not narrow ``CortexOS.execution.manifest``
refusals.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from CortexOS.dms.sql_guardrail import GuardrailResult, validate_sql
from CortexOS.execution.manifest import VerifiedManifest

#: Violation token when enforce_manifest refuses a candidate (C7-02).
MANIFEST_VIOLATION_PREFIX = "MANIFEST:"

#: CX-SC-01 — probe feedback tokens fed back to the generator as violations.
EXECUTION_ERROR_PREFIX = "EXECUTION_ERROR:"
EMPTY_RESULT_VIOLATION = (
    "EMPTY_RESULT: the query returned no rows — check filter literals use exact "
    "stored values and conditions are not over-restrictive; do not invent filter values"
)


@dataclass(slots=True)
class ProbeResult:
    """Outcome of executing a gate-passed candidate once (CX-SC-01)."""

    row_count: int = 0
    error: str | None = None


@dataclass(slots=True)
class ValidateGateResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    safe_sql: str | None = None
    explain_ok: bool = False
    attempts: int = 0
    explain_error: str | None = None
    manifest_refused: bool = False
    source_sql: str | None = None


class SqlGateAbstain(Exception):
    """Raised when validation/EXPLAIN retries are exhausted — caller must abstain."""

    def __init__(
        self,
        message: str,
        *,
        violations: list[str] | None = None,
        manifest_refused: bool = False,
        empty_result: bool = False,
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.violations = list(violations or [])
        self.manifest_refused = manifest_refused
        # CX-SC-01: a candidate passed the gate but the probe found no rows
        # (or failed to execute) — the caller abstains, never serves empty.
        self.empty_result = empty_result
        self.attempts = attempts

    def __str__(self) -> str:
        # FF-03 / dms#59 — callers interpolate `{exc}` into the envelope reason.
        # Exception.__str__ is only args[0], which dropped unknown-column /
        # unbound-table / EXPLAIN detail sitting on `.violations`.
        base = super().__str__()
        if not self.violations:
            return base
        return f"{base}: {', '.join(self.violations)}"


def explain_dry_run(
    con: Any, sql: str, params: Sequence[Any] | None = None
) -> tuple[bool, str]:
    """Run EXPLAIN without executing the query body. Returns (ok, detail)."""
    try:
        if params is not None:
            con.execute(f"EXPLAIN {sql}", params)
        else:
            con.execute(f"EXPLAIN {sql}")
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:400]


def run_gate(
    sql: str,
    semantic: dict[str, Any],
    *,
    con: Any | None = None,
    verified: VerifiedManifest | None = None,
) -> ValidateGateResult:
    """sqlglot allowlist, then enforce_manifest (if bound), then EXPLAIN.

    EXPLAIN sees only post-enforce SQL. ``source_sql`` is the pre-enforce
    candidate so ``execute_sql`` can enforce once. A ManifestError fails the
    candidate without EXPLAIN.
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

    source = guard.safe_sql
    safe_sql = source
    if verified is not None:
        from CortexOS.execution.manifest import ManifestError, enforce_manifest

        try:
            safe_sql = enforce_manifest(source, verified)
        except ManifestError as exc:
            return ValidateGateResult(
                passed=False,
                violations=[f"{MANIFEST_VIOLATION_PREFIX}{type(exc).__name__}:{exc.code}"],
                safe_sql=None,
                explain_ok=False,
                attempts=1,
                manifest_refused=True,
            )

    if con is None:
        return ValidateGateResult(
            passed=True,
            violations=[],
            safe_sql=safe_sql,
            explain_ok=True,
            attempts=1,
            source_sql=source,
        )

    ok, detail = explain_dry_run(con, safe_sql)
    if not ok:
        return ValidateGateResult(
            passed=False,
            violations=[f"EXPLAIN_FAILED:{detail}"],
            safe_sql=safe_sql,
            explain_ok=False,
            attempts=1,
            explain_error=detail,
            source_sql=source,
        )
    return ValidateGateResult(
        passed=True,
        violations=[],
        safe_sql=safe_sql,
        explain_ok=True,
        attempts=1,
        source_sql=source,
    )


def gate_with_retry(
    generate_fn: Callable[[list[str]], str | None],
    question: str,  # noqa: ARG001 — reserved for future schema-retrieval context
    semantic: dict[str, Any],
    *,
    con: Any | None = None,
    verified: VerifiedManifest | None = None,
    max_retries: int = 2,
    probe: Callable[[str], ProbeResult] | None = None,
) -> ValidateGateResult:
    """Call generate_fn up to max_retries+1 times, feeding prior violations back.

    ``generate_fn(prior_violations) -> sql | None``. Exhaustion raises SqlGateAbstain.
    ManifestError aborts that candidate (no EXPLAIN, no second try of the same
    SQL). A later generate_fn result may still pass.

    ``probe`` (CX-SC-01) runs only on SQL that already passed allowlist,
    manifest and EXPLAIN — it is never a way around a refusal. An execution
    error or zero rows becomes an ``EXECUTION_ERROR:`` / ``EMPTY_RESULT``
    violation fed back to the next attempt; exhaustion raises
    ``SqlGateAbstain(empty_result=True)``.
    """
    prior: list[str] = []
    last = ValidateGateResult(passed=False, attempts=0)
    saw_manifest = False
    saw_empty = False
    refused_sql: set[str] = set()
    for attempt in range(1, max_retries + 2):
        sql = generate_fn(prior)
        if not sql:
            last = ValidateGateResult(
                passed=False,
                violations=prior + ["NO_CANDIDATE"],
                attempts=attempt,
                manifest_refused=saw_manifest,
            )
            break
        last = run_gate(sql, semantic, con=con, verified=verified)
        last.attempts = attempt
        if last.manifest_refused:
            saw_manifest = True
            if sql in refused_sql:
                raise SqlGateAbstain(
                    "SQL validation gate exhausted retries",
                    violations=last.violations,
                    manifest_refused=True,
                )
            refused_sql.add(sql)
        if last.passed and last.safe_sql:
            if probe is None:
                return last
            feedback = _probe_violation(probe, last.safe_sql)
            if feedback is None:
                return last
            saw_empty = True
            last.passed = False
            last.violations = [feedback]
        prior = list(last.violations)
    raise SqlGateAbstain(
        "SQL validation gate exhausted retries",
        violations=last.violations,
        manifest_refused=saw_manifest or last.manifest_refused,
        empty_result=saw_empty,
        attempts=last.attempts,
    )


def _probe_violation(probe: Callable[[str], ProbeResult], safe_sql: str) -> str | None:
    """None when the probe found rows; else the violation to feed back."""
    try:
        res = probe(safe_sql)
    except Exception as exc:  # noqa: BLE001 — a probe failure is feedback, not a crash
        return f"{EXECUTION_ERROR_PREFIX} {str(exc)[:400]}"
    if res.error:
        return f"{EXECUTION_ERROR_PREFIX} {res.error[:400]}"
    if int(res.row_count or 0) <= 0:
        return EMPTY_RESULT_VIOLATION
    return None


def guard_result_from_gate(gate: ValidateGateResult) -> GuardrailResult:
    return GuardrailResult(
        passed=gate.passed,
        violations=list(gate.violations),
        safe_sql=gate.safe_sql,
    )


__all__ = [
    "EMPTY_RESULT_VIOLATION",
    "EXECUTION_ERROR_PREFIX",
    "MANIFEST_VIOLATION_PREFIX",
    "ProbeResult",
    "SqlGateAbstain",
    "ValidateGateResult",
    "explain_dry_run",
    "gate_with_retry",
    "guard_result_from_gate",
    "run_gate",
]
