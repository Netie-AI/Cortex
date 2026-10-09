"""C-LOOP-B (#304): run generated SQL, check the result, feed failures back.

At most ``SQL_LOOP_MAX_RETRIES`` (2) retries, then abstain with a reason from
``cortex_contract.answer.SQL_LOOP_ABSTAIN_REASONS``. Every attempt is stamped
``served_*`` (``SqlAttempt``) and the stamps are what the generator sees as
feedback: the SQL, the check that failed and its message, never result rows.

The generator, the gate and the executor are injected. This module picks no
model and opens no database; every candidate goes through ``gate`` before
``execute`` sees it, and ``execute`` only ever receives a passed gate result.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from cortex_contract.answer import (
    SQL_LOOP_MAX_RETRIES,
    AbstainReason,
    SqlAttempt,
    SqlLoop,
    SqlLoopCheck,
)

from CortexOS.dms.l2_plausibility import assess_plausibility
from CortexOS.dms.sql_validate_gate import ValidateGateResult

#: ``generate(question, prior_attempts) -> sql | None``. ``prior_attempts`` are
#: the stamped attempts so far, oldest first; empty on the first call.
SqlGenerator = Callable[[str, Sequence[SqlAttempt]], str | None]
SqlGate = Callable[[str], ValidateGateResult]
SqlExecute = Callable[[ValidateGateResult], list[dict[str, Any]]]

MAX_RETRIES = SQL_LOOP_MAX_RETRIES
_ERROR_CHARS = 300

_REASON_BY_CHECK: dict[SqlLoopCheck, AbstainReason] = {
    SqlLoopCheck.NO_CANDIDATE: AbstainReason.NO_SQL_CANDIDATE,
    SqlLoopCheck.GENERATOR_ERROR: AbstainReason.SQL_GENERATOR_FAILED,
    SqlLoopCheck.GATE: AbstainReason.SQL_GATE_REFUSED_AFTER_RETRIES,
    SqlLoopCheck.EXECUTE: AbstainReason.SQL_ERROR_AFTER_RETRIES,
    SqlLoopCheck.EMPTY: AbstainReason.EMPTY_RESULT_AFTER_RETRIES,
    SqlLoopCheck.SHAPE: AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES,
    SqlLoopCheck.WRONG_TYPE: AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES,
    SqlLoopCheck.NEGATIVE_COUNT: AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES,
    SqlLoopCheck.SUM_NOT_RECONCILED: AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES,
    SqlLoopCheck.ROWS_BEYOND_GRANT: AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES,
    SqlLoopCheck.PLAUSIBILITY: AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES,
}

#: The generator cannot improve on these with feedback; stop instead of retrying.
_TERMINAL = frozenset({SqlLoopCheck.NO_CANDIDATE, SqlLoopCheck.GENERATOR_ERROR})

_COUNT_TOKENS = frozenset({"count", "cnt", "num", "n"})
_TOTAL_LABELS = frozenset({"total", "grand total", "overall", "all"})
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


@dataclass(frozen=True, slots=True)
class CheckContext:
    """What the result checks may consult beyond the rows themselves.

    ``row_ceiling(sql)`` is the most rows the grant lets that SQL's tables hold
    (``None`` when unknown). ``leftover_literals(sql)`` names filter literals
    that do not resolve to a column's encoding.
    """

    granted_tables: tuple[str, ...] = ()
    row_ceiling: Callable[[str], int | None] | None = None
    leftover_literals: Callable[[str], list[str]] | None = None


@dataclass(frozen=True, slots=True)
class LoopResult:
    record: SqlLoop
    rows: list[dict[str, Any]] | None = None
    sql: str | None = None

    @property
    def answered(self) -> bool:
        return self.record.served_outcome == "answered"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scrub(message: object) -> str:
    """Error text for a stamp: quoted literals masked, so no cell value travels."""
    text = _QUOTED.sub("'?'", str(message)).strip()
    return text[:_ERROR_CHARS]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}


def _shape(rows: Sequence[Any]) -> str | None:
    if not all(isinstance(r, Mapping) for r in rows):
        return "result rows are not records"
    keys = [tuple(r.keys()) for r in rows]
    if not keys[0]:
        return "result has no columns"
    if any(k != keys[0] for k in keys):
        return "result rows do not share one set of columns"
    return None


def _wrong_type(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for col in rows[0]:
        values = [r[col] for r in rows if r[col] is not None]
        numeric = [_is_number(v) for v in values]
        if any(numeric) and not all(numeric):
            return f"column {col!r} mixes numeric and non-numeric values"
        if any(isinstance(v, float) and not math.isfinite(v) for v in values):
            return f"column {col!r} holds a non-finite number"
    return None


def _negative_count(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for col in rows[0]:
        if not (_tokens(col) & _COUNT_TOKENS):
            continue
        if any(_is_number(r[col]) and r[col] < 0 for r in rows):
            return f"count column {col!r} has a negative value"
    return None


def _sum_not_reconciled(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """A labelled total row must equal the sum of the other rows, per numeric column."""
    totals = [
        i
        for i, r in enumerate(rows)
        if any(isinstance(v, str) and v.strip().lower() in _TOTAL_LABELS for v in r.values())
    ]
    if len(totals) != 1 or len(rows) < 2:
        return None
    total = rows[totals[0]]
    parts = [r for i, r in enumerate(rows) if i != totals[0]]
    for col, value in total.items():
        if not _is_number(value) or not all(_is_number(p[col]) for p in parts):
            continue
        summed = sum(float(p[col]) for p in parts)
        if not math.isclose(float(value), summed, rel_tol=1e-6, abs_tol=0.01):
            return f"total row of column {col!r} does not equal the sum of its parts"
    return None


def _failed_check(
    question: str, sql: str, rows: Sequence[Any], ctx: CheckContext
) -> tuple[SqlLoopCheck, str] | None:
    """The first explicit check the result fails, or ``None`` when it passes all."""
    if not rows or all(
        isinstance(r, Mapping) and all(v is None for v in r.values()) for r in rows
    ):
        return SqlLoopCheck.EMPTY, "query returned no rows" if not rows else "every value is NULL"
    reason = _shape(rows)
    if reason:
        return SqlLoopCheck.SHAPE, reason
    for check, fn in (
        (SqlLoopCheck.WRONG_TYPE, _wrong_type),
        (SqlLoopCheck.NEGATIVE_COUNT, _negative_count),
        (SqlLoopCheck.SUM_NOT_RECONCILED, _sum_not_reconciled),
    ):
        reason = fn(rows)
        if reason:
            return check, reason
    if len(rows) > 1 and ctx.row_ceiling is not None:
        ceiling = ctx.row_ceiling(sql)
        if ceiling is not None and len(rows) > ceiling:
            return (
                SqlLoopCheck.ROWS_BEYOND_GRANT,
                f"{len(rows)} rows exceed the {ceiling} the granted tables hold",
            )
    trip = assess_plausibility(
        question,
        sql,
        rows,
        retrieved_tables=ctx.granted_tables or None,
        leftover_literals=ctx.leftover_literals(sql) if ctx.leftover_literals else None,
    )
    if not trip.ok:
        return SqlLoopCheck.PLAUSIBILITY, f"{trip.code}: {trip.reason}"
    return None


def _gated(gate: SqlGate, sql: str) -> ValidateGateResult:
    return gate(sql)


def unavailable() -> LoopResult:
    """Named abstain when no generator is registered. No attempt was made."""
    return LoopResult(
        SqlLoop(
            served_outcome="abstained",
            served_abstain_reason=AbstainReason.SQL_GENERATOR_UNAVAILABLE,
            served_retries=0,
        )
    )


def run_sql_loop(
    question: str,
    *,
    generate: SqlGenerator,
    gate: SqlGate,
    execute: SqlExecute,
    ctx: CheckContext | None = None,
) -> LoopResult:
    """Generate, gate, execute and check; retry with feedback; answer or abstain."""
    ctx = ctx or CheckContext()
    attempts: list[SqlAttempt] = []

    def stamp(
        n: int,
        sql: str | None,
        check: SqlLoopCheck,
        error: str | None,
        outcome: Literal["retry", "answered", "abstained"],
    ) -> None:
        attempts.append(
            SqlAttempt(
                served_attempt=n,
                served_sql=sql,
                served_check=check,
                served_error=error,
                served_outcome=outcome,
                served_at=_now(),
            )
        )

    last_check = SqlLoopCheck.NO_CANDIDATE
    for n in range(1, MAX_RETRIES + 2):
        final = n == MAX_RETRIES + 1
        sql: str | None = None
        rows: list[dict[str, Any]] | None = None
        failure: tuple[SqlLoopCheck, str] | None
        try:
            sql = generate(question, tuple(attempts))
        except Exception as exc:  # noqa: BLE001 — any generator failure is a named abstain
            failure = (SqlLoopCheck.GENERATOR_ERROR, _scrub(f"{type(exc).__name__}: {exc}"))
        else:
            if not isinstance(sql, str) or not sql.strip():
                sql = None
                failure = (SqlLoopCheck.NO_CANDIDATE, "generator returned no SQL")
            else:
                gated = _gated(gate, sql)
                if not gated.passed or not gated.safe_sql:
                    failure = (
                        SqlLoopCheck.GATE,
                        _scrub(", ".join(gated.violations) or "SQL gate refused the candidate"),
                    )
                else:
                    try:
                        rows = execute(gated)
                    except Exception as exc:  # noqa: BLE001 — a SQL error is fed back, not raised
                        failure = (SqlLoopCheck.EXECUTE, _scrub(f"{type(exc).__name__}: {exc}"))
                    else:
                        failure = _failed_check(question, sql, rows, ctx)
                        if failure is None:
                            stamp(n, sql, SqlLoopCheck.PASSED, None, "answered")
                            record = SqlLoop(
                                served_outcome="answered",
                                served_retries=n - 1,
                                served_attempts=attempts,
                            )
                            return LoopResult(record, rows, gated.source_sql or gated.safe_sql)
        check, error = failure
        last_check = check
        stop = final or check in _TERMINAL
        stamp(n, sql, check, error, "abstained" if stop else "retry")
        if stop:
            break
    return LoopResult(
        SqlLoop(
            served_outcome="abstained",
            served_abstain_reason=_REASON_BY_CHECK[last_check],
            served_retries=len(attempts) - 1,
            served_attempts=attempts,
        )
    )


__all__ = [
    "MAX_RETRIES",
    "CheckContext",
    "LoopResult",
    "SqlExecute",
    "SqlGate",
    "SqlGenerator",
    "run_sql_loop",
    "unavailable",
]
