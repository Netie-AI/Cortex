"""C7 — sqlglot + optional manifest enforce + EXPLAIN + bounded retry.

A grounded session runs ``enforce_manifest`` before EXPLAIN so the dry-run
never sees pre-enforce SQL (C7-02). Manifest refusals are not retried for
the same SQL. This module does not narrow ``CortexOS.execution.manifest``
refusals.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from CortexOS.dms.sql_guardrail import GuardrailResult, validate_sql
from CortexOS.execution.manifest import VerifiedManifest

#: Violation token when enforce_manifest refuses a candidate (C7-02).
MANIFEST_VIOLATION_PREFIX = "MANIFEST:"

# DuckDB prefixes its messages with the error class ("Binder Error: ...").
_DB_ERROR_CLASS = re.compile(r"^\s*([A-Za-z][A-Za-z ]{0,40}?) Error:\s*")
# The caret context ("LINE 1: ...") quotes the post-enforce SQL, row predicates included.
_LINE_CONTEXT = re.compile(r"\bLINE \d+:")
# A quoted literal, or one the 400-char cut left open.
_QUOTED = re.compile(r"'(?:[^']|'')*(?:'|$)")
_REDACTED_CHARS = 240


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
        raw_detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.violations = list(violations or [])
        self.manifest_refused = manifest_refused
        # Unredacted engine text, for the audit sink only. Never rendered.
        self.raw_detail = raw_detail

    def __str__(self) -> str:
        # FF-03 / dms#59 — callers interpolate `{exc}` into the envelope reason.
        # Exception.__str__ is only args[0], which dropped unknown-column /
        # unbound-table / EXPLAIN detail sitting on `.violations`.
        base = super().__str__()
        if not self.violations:
            return base
        return f"{base}: {', '.join(self.violations)}"


def redact_db_error(detail: object) -> str:
    """``CODE: message`` for a database error, safe to show a user or a model.

    The code is the engine's own error class (``BINDER_ERROR``), else
    ``DB_ERROR``. The ``LINE n:`` caret context is dropped and quoted literals
    are masked, so no post-enforce SQL, row-predicate literal or cell value
    travels. Double-quoted identifiers stay: they name what failed, so an
    error-fed retry can fix it. The raw text belongs to the audit sink.
    """
    text = str(detail or "")
    code = "DB_ERROR"
    match = _DB_ERROR_CLASS.match(text)
    if match:
        code = "_".join(match.group(1).upper().split()) + "_ERROR"
        text = text[match.end() :]
    text = _LINE_CONTEXT.split(text, 1)[0]
    text = " ".join(_QUOTED.sub("'?'", text).split())
    return f"{code}: {text[:_REDACTED_CHARS]}" if text else code


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
            violations=[f"EXPLAIN_FAILED:{redact_db_error(detail)}"],
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


def _output_lineage(
    gate: ValidateGateResult,
    semantic: dict[str, Any],
    verified: VerifiedManifest | None,
) -> list[str]:
    """Violations for output columns no granted table supplies. Empty when all derive.

    Granted tables are the verified grant's, else the semantic allowlist the
    gate already enforced. Read from the pre-enforce SQL: enforcement only
    narrows rows, it does not add columns.
    """
    from CortexOS.dms.sql_guardrail import _allowed_tables
    from CortexOS.dms.sql_lineage import lineage_violations

    sql = gate.source_sql or gate.safe_sql or ""
    if verified is not None:
        granted: set[str] = {str(name) for name in verified.manifest.row_predicates}
    else:
        granted = set(_allowed_tables(semantic))
    return lineage_violations(sql, granted)


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
    SQL). A later generate_fn result may still pass. A candidate that passes
    the gate but has an output column no granted table supplies (a literal, a
    VALUES list) is refused as ``OUTPUT_NOT_FROM_TABLE:<col>`` and fed back.
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
            lineage = _output_lineage(last, semantic, verified)
            if not lineage:
                return last
            last = ValidateGateResult(passed=False, violations=lineage, attempts=attempt)
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
    "redact_db_error",
    "run_gate",
]
