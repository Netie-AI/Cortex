"""PLAN-SQL-VOTE-01: model SQL is served only when a second, independent SQL agrees.

Off unless ``CORTEX_PLAN_SQL_VOTE=1`` (and the plan+SQL ladder is on). Measured
on BIRD Mini-Dev: two SQL candidates that return the same rows were right
about four times in five, and a candidate that no second one matched was
right about one time in four. So the ladder's first gated SQL is a candidate,
not an answer:

1. ``B``: the same model writes SQL for the question without the plan, through
   the same gates, with the ladder's error-fed retry. The same rows as the
   first candidate serve the first.
2. ``C``: otherwise the stronger route OpenVault configured writes it. The
   candidate whose rows ``C`` matches is served.
3. No two candidates agree: nothing is served and the ladder reconfirms.

A ``B`` that writes the same SQL text as the first candidate counts: on 500
BIRD questions those agreed answers were right 41 times in 49, better than
agreement between different SQL. Rows are compared as a set, numbers to four
decimals. This module only compares; the ladder writes, gates and runs.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

ENABLED_ENV = "CORTEX_PLAN_SQL_VOTE"
VOTE_SPLIT = "PLAN_SQL_VOTE_SPLIT"
SPLIT_DETAIL = "no two independent SQL readings of the question returned the same rows"


def vote_enabled() -> bool:
    return (os.environ.get(ENABLED_ENV) or "").strip().lower() in {"1", "true", "yes"}


def _value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)):
        return round(float(value), 4)
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def result_key(rows: list[dict[str, Any]]) -> frozenset[tuple[Any, ...]]:
    """The rows as a set of value tuples in column order. Column names are ignored."""
    return frozenset(tuple(_value(v) for v in row.values()) for row in rows)


def same_sql(first: str, second: str) -> bool:
    """True when two SQL texts differ only in case, whitespace or a trailing semicolon."""

    def _norm(sql: str) -> str:
        return " ".join((sql or "").split()).rstrip(";").strip().lower()

    return _norm(first) == _norm(second)


def agree(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> bool:
    return bool(first) and result_key(first) == result_key(second)


__all__ = [
    "ENABLED_ENV",
    "SPLIT_DETAIL",
    "VOTE_SPLIT",
    "agree",
    "result_key",
    "same_sql",
    "vote_enabled",
]
