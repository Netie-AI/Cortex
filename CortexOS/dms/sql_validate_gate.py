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
    ) -> None:
        super().__init__(message)
        self.violations = list(violations or [])
        self.manifest_refused = manifest_refused

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


def sql_for_explain(sql: str, *, verified: VerifiedManifest | None = None) -> str:
    """SQL that EXPLAIN (and fetch) may see.

    Grounded sessions return ``enforce_manifest`` output (C7-02) and raise
    ``ManifestError``. Ungrounded sessions return ``sql`` unchanged.
    """
    if verified is None:
        return sql
    from CortexOS.execution.manifest import enforce_manifest

    return enforce_manifest(sql, verified)


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

    from CortexOS.execution.manifest import ManifestError

    source = guard.safe_sql
    try:
        safe_sql = sql_for_explain(source, verified=verified)
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
) -> ValidateGateResult:
    """Call generate_fn up to max_retries+1 times, feeding prior violations back.

    ``generate_fn(prior_violations) -> sql | None``. Exhaustion raises SqlGateAbstain.
    ManifestError aborts that candidate (no EXPLAIN, no second try of the same
    SQL). A later generate_fn result may still pass.
    """
    prior: list[str] = []
    last = ValidateGateResult(passed=False, attempts=0)
    saw_manifest = False
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
            return last
        prior = list(last.violations)
    raise SqlGateAbstain(
        "SQL validation gate exhausted retries",
        violations=last.violations,
        manifest_refused=saw_manifest or last.manifest_refused,
    )


def guard_result_from_gate(gate: ValidateGateResult) -> GuardrailResult:
    return GuardrailResult(
        passed=gate.passed,
        violations=list(gate.violations),
        safe_sql=gate.safe_sql,
    )


__all__ = [
    "MANIFEST_VIOLATION_PREFIX",
    "SqlGateAbstain",
    "ValidateGateResult",
    "explain_dry_run",
    "gate_with_retry",
    "guard_result_from_gate",
    "run_gate",
    "sql_for_explain",
]
