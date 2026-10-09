"""C-LOOP-B (#304) ask seam: answer ``/v1/contract/ask`` through the SQL loop.

Off unless ``CORTEX_SQL_SELF_CORRECT=1``; when off, nothing here runs. The SQL
generator is registered by its owner (``register_sql_generator``); Cortex
never picks or calls a model here. Every candidate runs through the same
``run_gate`` and manifest-enforced ``execute_sql`` the contract ask already
uses, under the session's signed grant. The loop writes no memory.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

from CortexOS.dms.sql_self_correct import (
    CheckContext,
    LoopResult,
    SqlGenerator,
    run_sql_loop,
    unavailable,
)
from CortexOS.execution.manifest import VerifiedManifest

ENABLED_ENV = "CORTEX_SQL_SELF_CORRECT"
LAYER = "sql_self_correct"

_IDENT = re.compile(r"[a-z_][a-z0-9_]*")
_generator: SqlGenerator | None = None


def sql_self_correct_enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def register_sql_generator(generate: SqlGenerator) -> None:
    """Install the SQL generator the loop calls. Called by its owner, never by the engine."""
    global _generator
    _generator = generate


def clear_sql_generator() -> None:
    global _generator
    _generator = None


def _answered(question: str, result: LoopResult) -> dict[str, Any]:
    from CortexOS.dms.query_service import synthesize_answer

    rows = result.rows or []
    n = len(result.record.served_attempts)
    return {
        "answer": synthesize_answer(rows, question),
        "sql_used": result.sql,
        "audit_id": str(uuid.uuid4()),
        "route": LAYER,
        "row_count": len(rows),
        "rows": rows,
        "provenance": {
            "layer": LAYER,
            "badge": "session",
            "query_source": f"{LAYER}:attempt-{n}",
            "assumptions": (
                f"Generated SQL ran under this session's grant on attempt {n} after "
                "passing the SQL gate and every result check. Not a governed metric."
            ),
        },
        "sql_loop": result.record,
    }


def _abstained(result: LoopResult) -> dict[str, Any]:
    reason = result.record.served_abstain_reason
    name = reason.value if reason is not None else ""
    return {
        "answer": f"I can't answer that reliably ({name}). No result is shown.",
        "sql_used": None,
        "audit_id": str(uuid.uuid4()),
        "route": "abstain",
        "layer": "abstain",
        "badge": "abstain",
        "rows": None,
        "assumptions": f"SQL loop abstained: {name}",
        "sql_loop": result.record,
    }


def self_correct_answer(
    question: str,
    *,
    session_id: str,
    space_id: str | None,
    verified: VerifiedManifest,
) -> dict[str, Any] | None:
    """Loop answer for one contract ask, or ``None`` to keep the existing engine path.

    ``None`` when the session is ungrounded or the question is blocked: those
    keep their existing refusals rather than a loop-shaped one.
    """
    from CortexOS.dms.answer_engine import UngroundedSession, resolve_product_grant
    from CortexOS.dms.query_service import route_question

    try:
        grant, _, sources = resolve_product_grant(session_id, verified, space_id=space_id)
    except UngroundedSession:
        return None
    if route_question(question) == "blocked":
        return None

    generate = _generator
    if generate is None:
        return _abstained(unavailable())

    from CortexOS.dms.l2_plausibility import leftover_literals_via_port, sql_table_names
    from CortexOS.dms.sql_validate_gate import ValidateGateResult, run_gate
    from CortexOS.dms.warehouse_db import load_semantic_layer
    from CortexOS.execution.submit import execute_sql

    semantic = load_semantic_layer()

    def gate(sql: str) -> ValidateGateResult:
        return run_gate(sql, semantic, verified=grant)

    def execute(gated: ValidateGateResult) -> list[dict[str, Any]]:
        # Pre-enforce SQL: execute_sql runs enforce_manifest exactly once.
        rows, _, _ = execute_sql(grant, gated.source_sql or gated.safe_sql or "")
        return rows

    counts: dict[str, int | None] = {}

    def table_rows(table: str) -> int | None:
        if table not in counts:
            counts[table] = None
            gated = gate(f"SELECT COUNT(*) AS n FROM {table}")
            if gated.passed and gated.safe_sql:
                try:
                    counts[table] = int(execute(gated)[0]["n"])
                except Exception:  # noqa: BLE001 — unknown ceiling skips only this check
                    counts[table] = None
        return counts[table]

    def row_ceiling(sql: str) -> int | None:
        tables = [t for t in sql_table_names(sql) if t in sources and _IDENT.fullmatch(t)]
        sizes = [s for s in (table_rows(t) for t in tables) if s is not None]
        return max(sizes) if sizes and len(sizes) == len(tables) else None

    result = run_sql_loop(
        question,
        generate=generate,
        gate=gate,
        execute=execute,
        ctx=CheckContext(
            granted_tables=tuple(sources),
            row_ceiling=row_ceiling,
            leftover_literals=leftover_literals_via_port,
        ),
    )
    return _answered(question, result) if result.answered else _abstained(result)


__all__ = [
    "ENABLED_ENV",
    "clear_sql_generator",
    "register_sql_generator",
    "self_correct_answer",
    "sql_self_correct_enabled",
]
