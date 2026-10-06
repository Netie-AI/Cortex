"""C-LOOP (#291) ask seam: the analysis loop on ``POST /v1/contract/ask``.

Runs only when ``CORTEX_ANALYSIS_LOOP`` is on; otherwise contract ask is
unchanged. Everything the loop reads comes from the *signed* grant: the Space
that keys memory, the tables on the data map, and the session manifest every
SQL statement runs under (``run_gate`` then ``execute_sql``, the same gate as
the loop-off ask). The engine cascade is the SQL proposer; any model call in it
goes through OpenVault FreeRoute and is not chosen here.

``served_sql`` on a step is the SQL submitted to the session gate.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from cortex_contract.answer import LoopAbstainReason

from CortexOS.execution.manifest import VerifiedManifest
from CortexOS.loop.runner import Candidate, LoopPorts, LoopResult, run_analysis_loop
from CortexOS.memory.space_memory import get_space_memory, space_memory_enabled

_SERVED_FIELDS = ("served_provider", "served_model", "served_local", "served_reason")


def engine_candidate(result: Mapping[str, Any]) -> Candidate:
    """Read the engine's flat answer as a loop candidate. Abstains stay named."""
    route = str(result.get("route") or "").lower()
    badge = str(result.get("badge") or "").lower()
    layer = str(result.get("layer") or "").lower()
    tokens = {route, badge, layer}
    raw = result.get("assumptions")
    reason = "; ".join(str(a) for a in raw) if isinstance(raw, list) else str(raw or "")
    sql = result.get("sql_used") if isinstance(result.get("sql_used"), str) else None
    rows = [dict(r) for r in result.get("rows") or []]
    served_by = f"engine:{layer or route or 'unknown'}"
    abstain: LoopAbstainReason | None = None
    if "blocked" in tokens:
        abstain = LoopAbstainReason.POLICY_BLOCKED
    elif "refused" in tokens:
        abstain = LoopAbstainReason.ENGINE_REFUSED
    elif tokens & {"abstain", "needs_clarification"}:
        abstain = LoopAbstainReason.NO_TRUSTWORTHY_PATH
    return Candidate(
        sql=sql,
        rows=rows,
        served_by=served_by,
        abstain=abstain,
        reason=reason or ("engine abstained" if abstain else ""),
        envelope=dict(result),
    )


def _gated_executor(grant: VerifiedManifest | None, semantic: Mapping[str, Any]):
    def _execute(sql: str) -> list[dict[str, Any]]:
        from CortexOS.dms.sql_validate_gate import SqlGateAbstain, run_gate
        from CortexOS.execution.submit import execute_sql

        if grant is None:
            raise SqlGateAbstain("no session grant to run SQL under", violations=["UNGROUNDED"])
        gate = run_gate(sql, dict(semantic))
        if not gate.passed or not gate.safe_sql:
            raise SqlGateAbstain("SQL failed the gate", violations=list(gate.violations))
        rows, _, _ = execute_sql(grant, gate.safe_sql)
        return rows

    return _execute


def _memory_fields(result: LoopResult) -> dict[str, Any]:
    return {
        "memory_ids_read": result.memory_ids_read,
        "memory_reads": [
            {**entry.provenance(), "served_at": served_at}
            for entry, served_at in {e.id: (e, at) for e, at in result.memory_reads}.values()
        ],
        "reused": result.reused,
    }


def _abstain_envelope(result: LoopResult, audit_id: str) -> dict[str, Any]:
    assert result.abstain_reason is not None
    code = result.abstain_reason.value
    out: dict[str, Any] = {
        "answer": f"I can't answer that with confidence ({code}: {result.detail}).",
        "sql_used": None,
        "audit_id": audit_id,
        "route": "abstain",
        "row_count": 0,
        "rows": [],
        "layer": "abstain",
        "badge": "abstain",
        "assumptions": f"analysis loop abstain: {code}: {result.detail}",
        "suggestions": [],
    }
    cand = result.candidate
    if cand is not None:
        if cand.abstain is not None and cand.envelope:
            # The engine's own refusal text names the sources this session can use.
            out["answer"] = cand.envelope.get("answer") or out["answer"]
            out["suggestions"] = list(cand.envelope.get("suggestions") or [])
        for key in _SERVED_FIELDS:
            if key in cand.envelope:
                out[key] = cand.envelope[key]
    return out


def _answer_envelope(result: LoopResult, audit_id: str, question: str) -> dict[str, Any]:
    from CortexOS.dms.query_service import synthesize_answer

    cand = result.candidate
    assert cand is not None and cand.sql
    if not result.reused:
        return dict(cand.envelope)
    return {
        "answer": synthesize_answer(cand.rows, question),
        "sql_used": cand.sql,
        "audit_id": audit_id,
        "route": "memory_reuse",
        "row_count": len(cand.rows),
        "rows": cand.rows,
        "provenance": {
            "layer": "memory_reuse",
            "badge": "session",
            "query_source": cand.served_by,
            "assumptions": (
                f"Re-ran a {cand.envelope.get('validation')} solution from this Space on "
                "current data, then checked it. Reuse is not a validation."
            ),
        },
    }


def loop_ask(
    question: str,
    *,
    session_id: str,
    space_id: str | None,
    verified: VerifiedManifest,
    scored_pack_id: str | None = None,
) -> dict[str, Any]:
    """Flat answer dict for one contract ask, carrying ``analysis_loop``."""
    from CortexOS.dms.answer_engine import UngroundedSession, resolve_product_grant
    from CortexOS.dms.answer_engine import answer as answer_engine
    from CortexOS.dms.query_service import route_question
    from CortexOS.dms.warehouse_db import load_semantic_layer

    audit_id = str(uuid.uuid4())
    grant: VerifiedManifest | None = None
    granted: tuple[str, ...] = ()
    grant_error = ""
    try:
        grant, _, sources = resolve_product_grant(session_id, verified, space_id=space_id)
        granted = tuple(sources)
    except UngroundedSession as exc:
        grant_error = str(exc) or "no session grant is bound"
    space = ((grant.manifest.space_id or "").strip() if grant else "") or None
    semantic = load_semantic_layer()

    def _propose(q: str) -> Candidate:
        return engine_candidate(
            answer_engine(
                q,
                session_id=session_id,
                space_id=space_id,
                verified=verified,
                require_grounding=True,
                stamp_l2_route=True,
            )
        )

    ports = LoopPorts(
        space_id=space,
        granted=granted,
        schema=semantic,
        execute=_gated_executor(grant, semantic),
        propose=_propose,
        memory=get_space_memory() if space_memory_enabled() and space else None,
        grant_error=grant_error,
        blocked=route_question(question) == "blocked",
    )
    result = run_analysis_loop(
        question, ports=ports, audit_id=audit_id, scored_pack_id=scored_pack_id
    )
    if result.outcome == "answer":
        data = _answer_envelope(result, audit_id, question)
    else:
        data = _abstain_envelope(result, audit_id)
    data.update(_memory_fields(result))
    data["analysis_loop"] = result.record()
    return data
