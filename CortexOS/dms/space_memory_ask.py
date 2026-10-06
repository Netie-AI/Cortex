"""C-MEM (#290) ask seam: per-Space solution reuse and ``memory_ids_read``.

Memory is keyed by the Space the *signed* grant names, never by a caller
string, so a session bound to Space B cannot read Space A's memory. A reused
solution re-runs its SQL through the same gate the contract ask uses
(``run_gate`` then ``execute_sql`` under the session manifest) and is no wider
than ``/v1/contract/submit`` already is for that session.
"""

from __future__ import annotations

import uuid
from typing import Any

from CortexOS.execution.manifest import VerifiedManifest
from CortexOS.memory.space_memory import (
    SolutionReuse,
    get_space_memory,
    space_memory_enabled,
)

ASK_ACTOR = "cortex:contract-ask"


def memory_off() -> dict[str, Any]:
    return {"memory_ids_read": [], "memory_reads": [], "reused": False}


def _fields(reuse: SolutionReuse) -> dict[str, Any]:
    read_stamp = reuse.stamps[0]
    return {
        "memory_ids_read": [reuse.entry.id],
        "memory_reads": [{**reuse.entry.provenance(), "served_at": read_stamp.served_at}],
        "reused": reuse.ok,
    }


def try_solution_reuse(
    question: str,
    *,
    session_id: str,
    space_id: str | None,
    verified: VerifiedManifest,
    scored_pack_id: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return ``(answer_or_None, memory_fields)`` for one contract ask."""
    if not space_memory_enabled():
        return None, memory_off()
    from CortexOS.dms.answer_engine import UngroundedSession, resolve_product_grant
    from CortexOS.dms.query_service import route_question, synthesize_answer

    try:
        grant, _, _ = resolve_product_grant(session_id, verified, space_id=space_id)
    except UngroundedSession:
        return None, memory_off()
    space = (grant.manifest.space_id or "").strip()
    if not space or route_question(question) == "blocked":
        return None, memory_off()

    def _execute(sql: str) -> list[dict[str, Any]]:
        from CortexOS.dms.sql_validate_gate import SqlGateAbstain, run_gate
        from CortexOS.dms.warehouse_db import load_semantic_layer
        from CortexOS.execution.submit import execute_sql

        gate = run_gate(sql, load_semantic_layer())
        if not gate.passed or not gate.safe_sql:
            raise SqlGateAbstain("reused SQL failed the gate", violations=list(gate.violations))
        rows, _, _ = execute_sql(grant, gate.safe_sql)
        return rows

    reuse = get_space_memory().reuse_solution(
        space_id=space,
        question=question,
        execute=_execute,
        actor=ASK_ACTOR,
        scored_pack_id=scored_pack_id,
    )
    if reuse is None:
        return None, memory_off()
    fields = _fields(reuse)
    if not reuse.ok or reuse.rows is None:
        return None, fields
    rows = reuse.rows
    entry = reuse.entry
    return (
        {
            "answer": synthesize_answer(rows, question),
            "sql_used": reuse.sql,
            "audit_id": str(uuid.uuid4()),
            "route": "memory_reuse",
            "row_count": len(rows),
            "rows": rows,
            "provenance": {
                "layer": "memory_reuse",
                "badge": "session",
                "query_source": f"memory:{entry.id}@v{entry.version}",
                "assumptions": (
                    f"Re-ran a {entry.validation} solution from this Space on current data. "
                    "Reuse is not a validation."
                ),
            },
            **fields,
        },
        fields,
    )
