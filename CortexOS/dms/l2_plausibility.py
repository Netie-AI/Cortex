"""C7-03 — plausibility after L2 execute, before synthesize.

Pass or abstain only. Does not generate SQL, rewrite SQL, or call
``enforce_manifest``. Any trip returns empty rows to the caller.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

SCORE_CUT = 0.55

_ASSERTED = re.compile(
    r"\b(top|highest|lowest|rank|which|above|below|greater|less than|at least|"
    r"over|under)\b",
    re.I,
)
_SCALAR = re.compile(r"\b(how many|count of|average|avg\b|total\b|sum of)\b", re.I)


@dataclass(slots=True)
class PlausibilityResult:
    ok: bool
    code: str = ""
    reason: str = ""


def sql_table_names(sql: str) -> set[str]:
    """Physical table names in ``sql``. Empty on parse failure (fail closed)."""
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except SqlglotError:
        return set()
    if tree is None:
        return set()
    names: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = (table.name or "").strip().lower()
        if name:
            names.add(name)
    return names


def assess_plausibility(
    question: str,
    sql: str,
    rows: Sequence[dict[str, Any]],
    *,
    retrieved_tables: Sequence[str] | None = None,
    leftover_literals: Sequence[str] | None = None,
    score: float | None = None,
    score_cut: float = SCORE_CUT,
) -> PlausibilityResult:
    """Fail-closed checks. Order is load-bearing: 1-4 override a passing score."""
    n = len(rows)
    q = question or ""

    if n == 0 and _ASSERTED.search(q):
        return PlausibilityResult(
            ok=False,
            code="implausible_empty",
            reason="empty-success: question asserted matches but execute returned no rows",
        )

    if _SCALAR.search(q) and n > 1:
        return PlausibilityResult(
            ok=False,
            code="implausible_shape",
            reason="shape: scalar question returned a listing",
        )
    if n == 1 and rows:
        row = rows[0]
        if len(row) == 1:
            key = next(iter(row))
            val = row[key]
            if isinstance(val, (int, float)) and not _SCALAR.search(q) and _ASSERTED.search(q):
                if key.lower() in {"value", "col0", "v", "n"} or key.startswith("_"):
                    return PlausibilityResult(
                        ok=False,
                        code="implausible_shape",
                        reason="shape: listing question returned a single unlabeled number",
                    )

    retrieved = {t.strip().lower() for t in (retrieved_tables or ()) if t and t.strip()}
    used = sql_table_names(sql)
    if retrieved and used and used.isdisjoint(retrieved):
        return PlausibilityResult(
            ok=False,
            code="implausible_tables",
            reason="retrieval miss: SQL tables are disjoint from schema retrieval",
        )
    if retrieved and not used:
        return PlausibilityResult(
            ok=False,
            code="implausible_tables",
            reason="retrieval miss: SQL tables could not be analysed",
        )

    leftovers = [item for item in (leftover_literals or ()) if item]
    if leftovers:
        return PlausibilityResult(
            ok=False,
            code="implausible_literal",
            reason=f"literal leftover: {leftovers[0]}",
        )

    if score is not None and score < score_cut:
        return PlausibilityResult(
            ok=False,
            code="low_confidence",
            reason=f"low_confidence: score {score} below {score_cut}",
        )
    return PlausibilityResult(ok=True)


def leftover_literals_via_port(sql: str) -> list[str]:
    """Ask the L2 port for un-normalized literals. Engine never imports packs."""
    from CortexOS.dms.l2_generation import L2NotRegistered, resolve_l2_generation

    try:
        port = resolve_l2_generation()
    except L2NotRegistered:
        return []
    fn = getattr(port, "leftover_literals", None)
    if not callable(fn):
        return []
    try:
        return [str(item) for item in (fn(sql) or []) if item]
    except Exception:  # noqa: BLE001 — fail closed rather than skip the check
        return ["LEFTOVER_CHECK_FAILED"]


__all__ = [
    "PlausibilityResult",
    "SCORE_CUT",
    "assess_plausibility",
    "leftover_literals_via_port",
    "sql_table_names",
]
