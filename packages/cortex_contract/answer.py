from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Badge(str, Enum):
    CERTIFIED = "certified"
    GOVERNED_METRIC = "governed_metric"
    QUERY_SKILL = "query_skill"
    SESSION = "session"
    ABSTAIN = "abstain"
    BLOCKED = "blocked"


class AbstainReason(str, Enum):
    NO_TRUSTWORTHY_PATH = "no_trustworthy_path"
    POLICY_BLOCK = "policy_block"
    NEEDS_CLARIFICATION = "needs_clarification"
    # 1.5.0 C-LOOP-B: why the self-correcting SQL loop abstained.
    SQL_ERROR_AFTER_RETRIES = "sql_error_after_retries"
    SQL_GATE_REFUSED_AFTER_RETRIES = "sql_gate_refused_after_retries"
    EMPTY_RESULT_AFTER_RETRIES = "empty_result_after_retries"
    IMPLAUSIBLE_RESULT_AFTER_RETRIES = "implausible_result_after_retries"
    NO_SQL_CANDIDATE = "no_sql_candidate"
    SQL_GENERATOR_FAILED = "sql_generator_failed"
    SQL_GENERATOR_UNAVAILABLE = "sql_generator_unavailable"


#: The only reasons a SQL-loop abstain may carry. A generic reason is refused.
SQL_LOOP_ABSTAIN_REASONS: frozenset[AbstainReason] = frozenset(
    {
        AbstainReason.SQL_ERROR_AFTER_RETRIES,
        AbstainReason.SQL_GATE_REFUSED_AFTER_RETRIES,
        AbstainReason.EMPTY_RESULT_AFTER_RETRIES,
        AbstainReason.IMPLAUSIBLE_RESULT_AFTER_RETRIES,
        AbstainReason.NO_SQL_CANDIDATE,
        AbstainReason.SQL_GENERATOR_FAILED,
        AbstainReason.SQL_GENERATOR_UNAVAILABLE,
    }
)

#: First try plus at most two retries.
SQL_LOOP_MAX_RETRIES = 2


class SqlLoopCheck(str, Enum):
    """1.5.0: the check that decided one SQL-loop attempt."""

    NO_CANDIDATE = "no_candidate"
    GENERATOR_ERROR = "generator_error"
    GATE = "gate"
    EXECUTE = "execute"
    EMPTY = "empty"
    SHAPE = "shape"
    WRONG_TYPE = "wrong_type"
    NEGATIVE_COUNT = "negative_count"
    SUM_NOT_RECONCILED = "sum_not_reconciled"
    ROWS_BEYOND_GRANT = "rows_beyond_grant"
    PLAUSIBILITY = "plausibility"
    PASSED = "passed"


class SqlAttempt(BaseModel):
    """1.5.0: one stamped attempt of the self-correcting SQL loop."""

    served_attempt: int = Field(ge=1)
    served_sql: str | None = None
    served_check: SqlLoopCheck
    served_error: str | None = None
    served_outcome: Literal["retry", "answered", "abstained"]
    served_at: str


class SqlLoop(BaseModel):
    """1.5.0 C-LOOP-B: every attempt of the loop and its final outcome.

    ``answered`` only after an attempt passed every check; otherwise
    ``abstained`` with a reason from ``SQL_LOOP_ABSTAIN_REASONS``.
    """

    served_outcome: Literal["answered", "abstained"]
    served_abstain_reason: AbstainReason | None = None
    served_retries: int = Field(ge=0)
    served_attempts: list[SqlAttempt] = Field(default_factory=list)

    @model_validator(mode="after")
    def _honest(self) -> SqlLoop:
        _require_named_abstain(self)
        _require_retry_cap(self)
        return self


def _require_named_abstain(loop: SqlLoop) -> None:
    attempts = loop.served_attempts
    if loop.served_outcome == "abstained":
        if loop.served_abstain_reason not in SQL_LOOP_ABSTAIN_REASONS:
            raise ValueError(
                f"SQL-loop abstain needs a named reason, got {loop.served_abstain_reason!r}"
            )
        if any(a.served_outcome == "answered" for a in attempts):
            raise ValueError("an abstained SQL loop has no answered attempt")
        if attempts and attempts[-1].served_outcome != "abstained":
            raise ValueError("the last attempt of an abstained SQL loop is abstained")
        return
    if loop.served_abstain_reason is not None:
        raise ValueError("an answered SQL loop carries no abstain reason")
    last = attempts[-1] if attempts else None
    if last is None or last.served_check != SqlLoopCheck.PASSED or last.served_outcome != "answered":
        raise ValueError("a SQL loop answers only from an attempt that passed every check")
    if any(a.served_outcome != "retry" for a in attempts[:-1]):
        raise ValueError("every attempt before the answered one is a retry")


def _require_retry_cap(loop: SqlLoop) -> None:
    attempts = loop.served_attempts
    if len(attempts) > SQL_LOOP_MAX_RETRIES + 1:
        raise ValueError(
            f"SQL loop made {len(attempts) - 1} retries; at most {SQL_LOOP_MAX_RETRIES}"
        )
    if loop.served_retries != max(len(attempts) - 1, 0):
        raise ValueError("served_retries does not match the stamped attempts")
    if [a.served_attempt for a in attempts] != list(range(1, len(attempts) + 1)):
        raise ValueError("SQL-loop attempts are numbered 1..n in order")


class Provenance(BaseModel):
    layer: str
    badge: Badge
    metric_id: str | None = None
    query_source: str | None = None
    assumptions: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    session_id: str = "demo"
    space_id: str | None = None
    # 1.4.0: set on every ask of a scored round. Solution memory stays empty and
    # nothing is written to memory while it is set.
    scored_pack_id: str | None = None


class MemoryRead(BaseModel):
    """1.4.0: provenance of one per-Space memory entry an answer read."""

    id: str
    kind: str
    space_id: str
    source: str
    version: int
    written_at: str
    served_at: str


class ContributingSource(BaseModel):
    """One source card for the Sources panel (architecture §4.7 / §4.8)."""

    ref_id: str
    container: str | None = None
    member: str | None = None
    kind: str | None = None
    row_count: int | None = None
    contribution: float | None = None


class Answer(BaseModel):
    answer: str
    sql_used: str | None = None
    audit_id: str
    route: str
    row_count: int | None = None
    rows: list[dict[str, Any]] | None = None
    provenance: Provenance
    suggestions: list[str] = Field(default_factory=list)
    # T7 / contract 1.2.0 additive — older clients ignore these.
    answer_id: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    contributing_sources: list[ContributingSource] = Field(default_factory=list)
    drillthrough_token: str | None = None
    # 1.3.0: actual FreeRoute response identity for model-served L2 answers.
    served_provider: str | None = None
    served_model: str | None = None
    served_local: bool = False
    served_reason: str | None = None
    # 1.4.0 C-MEM: per-Space memory this answer read. [] when memory is off or
    # nothing was read. ``reused`` marks a stored solution re-run on current
    # data; it is never a validation by itself.
    memory_ids_read: list[str] = Field(default_factory=list)
    memory_reads: list[MemoryRead] = Field(default_factory=list)
    reused: bool = False
    # 1.5.0 C-LOOP-B: set only when the self-correcting SQL loop answered or
    # abstained. Left off the wire otherwise, so a 1.4 answer is unchanged.
    sql_loop: SqlLoop | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _sql_loop_serves_only_checked_rows(self) -> Answer:
        _require_no_abstained_rows(self)
        return self


def _require_no_abstained_rows(answer: Answer) -> None:
    loop = answer.sql_loop
    if loop is None:
        return
    if loop.served_outcome == "abstained":
        if answer.provenance.badge != Badge.ABSTAIN or answer.rows or answer.sql_used:
            raise ValueError("an abstained SQL loop serves no rows, no SQL and an ABSTAIN badge")
    elif not answer.sql_used or answer.provenance.badge in {Badge.ABSTAIN, Badge.BLOCKED}:
        raise ValueError("an answered SQL loop serves the SQL of its passing attempt")


class DrillthroughRequest(BaseModel):
    token: str = Field(min_length=1)


class DrillthroughResponse(BaseModel):
    answer_id: str
    session_id: str
    sql_used: str
    approximate: bool = False
    row_count: int
    total_count: int | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
