"""C-LOOP (#291) injected seams of the analysis-loop orchestrator.

The orchestrator owns step order, the checks it must not skip, and the stamps.
Three stages are supplied by the caller and stubbed in tests:

* :class:`Generator` (C-LOOP-A #303) — plan context in, one SQL candidate out.
* :class:`SelfCorrect` (C-LOOP-B #304) — a failed attempt in, a new candidate,
  a named abstain, or ``None`` (abstain on the failure) out. It decides how many
  times to retry; every candidate it returns is run, checked and evaluated again.
* :class:`Packager` (C-LOOP-C #305) — checked rows in, the answer envelope out.
  Never called on an abstain.

A candidate carries SQL, never rows: the orchestrator runs every statement
through the executor the caller injects (the session gate and manifest).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

_NAMED = re.compile(r"^[a-z][a-z0-9_]*$")


class LoopReason(str, Enum):
    """Abstain codes the orchestrator itself raises (engine-side, not on the wire)."""

    UNGROUNDED_SESSION = "ungrounded_session"
    POLICY_BLOCKED = "policy_blocked"
    ENGINE_REFUSED = "engine_refused"
    NO_TRUSTWORTHY_PATH = "no_trustworthy_path"
    NOT_A_SQL_ANSWER = "not_a_sql_answer"
    TOOL_FAILED = "tool_failed"
    SQL_FAILED = "sql_failed"
    SQL_NOT_ANALYSABLE = "sql_not_analysable"
    UNGRANTED_TABLE = "ungranted_table"
    EMPTY_RESULT = "empty_result"
    NULL_RESULT = "null_result"
    NON_FINITE_VALUE = "non_finite_value"
    FORMULA_MISMATCH = "formula_mismatch"
    FORMULA_UNVERIFIABLE = "formula_unverifiable"
    PACKAGE_FAILED = "package_failed"


def named(reason: str | LoopReason) -> str:
    """``reason`` as a snake_case code; anything else is not a named abstain."""
    code = reason.value if isinstance(reason, LoopReason) else reason
    if not isinstance(code, str) or not _NAMED.match(code):
        raise TypeError(f"an abstain must name a snake_case reason code, got {reason!r}")
    return code


@dataclass(frozen=True, slots=True)
class PlanContext:
    """What the orchestrator knows before any SQL: all of it from the signed grant."""

    question: str
    space_id: str | None
    granted: tuple[str, ...]
    data_map: Mapping[str, Any]
    table_memory: Sequence[Mapping[str, Any]] = ()
    formula_memory: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True, slots=True)
class Candidate:
    """SQL one source proposes, or the named reason it declined."""

    sql: str | None
    served_by: str
    abstain: str | None = None
    reason: str = ""
    envelope: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Failure:
    step: str
    reason: str
    detail: str
    sql: str | None


@dataclass(frozen=True, slots=True)
class Abstained:
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class Checked:
    """Rows that passed check and evaluate, and what the tools made of them."""

    candidate: Candidate
    sql: str
    rows: list[dict[str, Any]]
    reused: bool
    tool_outputs: Mapping[str, Mapping[str, Any]]


class Generator(Protocol):
    def __call__(self, context: PlanContext) -> Candidate: ...


class SelfCorrect(Protocol):
    def __call__(
        self, context: PlanContext, candidate: Candidate, failure: Failure
    ) -> Candidate | Abstained | None: ...


class Packager(Protocol):
    def __call__(self, context: PlanContext, checked: Checked) -> dict[str, Any]: ...


def no_self_correct(context: PlanContext, candidate: Candidate, failure: Failure) -> None:
    """Default until C-LOOP-B: no retry, abstain on the failure's own reason."""
    return None


__all__ = [
    "Abstained",
    "Candidate",
    "Checked",
    "Failure",
    "Generator",
    "LoopReason",
    "Packager",
    "PlanContext",
    "SelfCorrect",
    "named",
    "no_self_correct",
]
