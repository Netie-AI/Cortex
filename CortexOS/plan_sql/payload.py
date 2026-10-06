"""C-LOOP-A (#303): the DMS payload as a plan+SQL request, and its grant check.

Pure functions only. A payload can narrow what the signed grant allows, never
widen it: every selected table and both ends of every join must be a table the
grant names, or the ask is refused before any model sees the schema.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from cortex_contract.answer import AskPayload

PAYLOAD_WIDENS_GRANT = "PLAN_SQL_PAYLOAD_WIDENS_GRANT"
JOIN_OUTSIDE_PAYLOAD = "PLAN_SQL_JOIN_OUTSIDE_PAYLOAD"

MAX_PLAN_LINES = 8
MAX_LINE_CHARS = 240

_PLAN_SYSTEM = (
    "You plan one read-only analytics query over the tables given. "
    "Reply with a numbered plan of 2 to 6 short steps, one per line. "
    "Name the tables, joins, filters, grouping and aggregation each step uses. "
    "Use only the tables, columns and joins listed. Do not write SQL."
)
_SQL_SYSTEM = (
    "You write a single DuckDB SELECT that carries out the plan given. "
    "Use ONLY the tables, columns and joins listed. "
    "No DDL/DML. No comments. Prefer LIMIT 50. "
    "Return SQL only."
)


def _norm(name: str) -> str:
    return name.strip().lower()


@dataclass(frozen=True)
class PlanSqlRequest:
    """One question plus the payload DMS selected for it."""

    question: str
    payload: AskPayload

    def table_names(self) -> frozenset[str]:
        return frozenset(_norm(t.name) for t in self.payload.tables)


def widening(request: PlanSqlRequest, granted: Iterable[str]) -> list[str]:
    """Why this payload reaches past the grant. ``[]`` when it stays inside."""
    allowed = {_norm(g) for g in granted}
    selected = request.table_names()
    out = [
        f"{PAYLOAD_WIDENS_GRANT}:{name}"
        for name in sorted(selected)
        if name not in allowed
    ]
    for join in request.payload.joins:
        for end in (join.left_table, join.right_table):
            name = _norm(end)
            if name not in allowed:
                out.append(f"{PAYLOAD_WIDENS_GRANT}:{name}")
            elif name not in selected:
                out.append(f"{JOIN_OUTSIDE_PAYLOAD}:{name}")
    return list(dict.fromkeys(out))


def schema_block(request: PlanSqlRequest) -> str:
    lines = ["TABLES (use only these):"]
    for table in request.payload.tables:
        cols = ", ".join(
            " ".join(p for p in (c.name, c.type or "") if p)
            + (f" -- {c.description}" if c.description else "")
            for c in table.columns
        )
        note = f"  # {table.description}" if table.description else ""
        lines.append(f"- {table.name}({cols}){note}")
    if request.payload.joins:
        lines.append("JOINS (ontology; use only these):")
        for j in request.payload.joins:
            rel = f" ({j.relation})" if j.relation else ""
            lines.append(
                f"- {j.left_table}.{j.left_column} = {j.right_table}.{j.right_column}{rel}"
            )
    return "\n".join(lines)


def plan_lines(text: str) -> tuple[str, ...]:
    """Numbered plan steps from model text, bounded. ``()`` when there are none."""
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*").strip()
        if not line or line.startswith("```"):
            continue
        out.append(" ".join(line.split())[:MAX_LINE_CHARS])
        if len(out) >= MAX_PLAN_LINES:
            break
    return tuple(out)


def plan_messages(request: PlanSqlRequest) -> list[dict[str, Any]]:
    user = f"{schema_block(request)}\n\nQUESTION: {request.question}"
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": user},
    ]


def sql_messages(
    request: PlanSqlRequest,
    plan: Sequence[str],
    *,
    prior_violations: Sequence[str] = (),
) -> list[dict[str, Any]]:
    parts = [
        schema_block(request),
        "",
        "PLAN:",
        *plan,
        "",
        f"QUESTION: {request.question}",
        "",
        "Emit one DuckDB SELECT.",
    ]
    if prior_violations:
        parts.append("PREVIOUS VALIDATION ERRORS (fix these):")
        parts.extend(f"- {v}" for v in list(prior_violations)[:8])
    return [
        {"role": "system", "content": _SQL_SYSTEM},
        {"role": "user", "content": "\n".join(parts)},
    ]


__all__ = [
    "JOIN_OUTSIDE_PAYLOAD",
    "PAYLOAD_WIDENS_GRANT",
    "PlanSqlRequest",
    "plan_lines",
    "plan_messages",
    "schema_block",
    "sql_messages",
    "widening",
]
