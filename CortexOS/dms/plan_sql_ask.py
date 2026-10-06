"""C-LOOP-A (#303) ask seam: DMS payload -> plan -> SQL -> gate -> execute.

Off unless ``CORTEX_PLAN_SQL=1`` and the ask carries ``dms_payload``; with the
flag off ``/v1/contract/ask`` never enters this module's answer path.

Order, each step stamped (``StepStamp``):

1. ``grant``: the signed session grant, resolved the same way the contract ask
   does (``resolve_product_grant``). Unbound, expired, self-issued or wrong-Space
   grants are refused.
2. ``payload``: every selected table and both ends of every join must be in the
   grant. A payload that widens the grant is refused before any model call.
3. ``plan`` / ``sql``: two model calls through the injected
   ``PlanSqlGenerator`` (default: OpenVault FreeRoute). A step whose route stamp
   FreeRoute did not journal, or whose response omitted ``served_provider`` /
   ``served_model``, is refused.
4. ``gate``: the existing ``run_gate`` (sqlglot allowlist + ``enforce_manifest``
   under the grant), then the SQL may only name payload tables.
5. ``execute``: ``execute_sql`` under the same grant (enforce + EXPLAIN again).

Scored rounds (``scored_pack_id`` / ``CORTEX_SCORED_ROUND``, the #290 guard) run
FreeRoute on the benchmark split so the route store never learns from them.
This path writes nothing to per-Space memory.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from cortex_contract.answer import AskRequest

from CortexOS.integrations import freeroute
from CortexOS.plan_sql import payload as plan_payload
from CortexOS.plan_sql.generator import (
    FreeRoutePlanSqlGenerator,
    ModelStep,
    PlanSqlGenerator,
    StepStamp,
)

ENABLED_ENV = "CORTEX_PLAN_SQL"
LAYER = "plan_sql"

UNGROUNDED = "PLAN_SQL_UNGROUNDED"
NOT_FREEROUTE = "PLAN_SQL_NOT_FREEROUTE"
ROUTE_STAMP_MISSING = "PLAN_SQL_ROUTE_STAMP_MISSING"
NO_SELECT = "PLAN_SQL_NO_SELECT"
GATE_REFUSED = "PLAN_SQL_GATE_REFUSED"
MANIFEST_REFUSED = "PLAN_SQL_MANIFEST_REFUSED"
OUTSIDE_PAYLOAD = "PLAN_SQL_OUTSIDE_PAYLOAD"
MODEL_UNAVAILABLE = "PLAN_SQL_MODEL_UNAVAILABLE"
EXECUTE_FAILED = "PLAN_SQL_EXECUTE_FAILED"
EMPTY_RESULT = "PLAN_SQL_EMPTY_RESULT"

CAVEAT = (
    "Plan and SQL are model-generated through OpenVault FreeRoute; the SQL passed "
    "the Cortex SQL gate and ran under this session's grant. Not validated for accuracy."
)


def plan_sql_enabled() -> bool:
    return (os.environ.get(ENABLED_ENV) or "").strip().lower() in {"1", "true", "yes"}


def default_generator() -> PlanSqlGenerator:
    return FreeRoutePlanSqlGenerator()


def try_plan_sql(body: AskRequest, *, verified: Any) -> dict[str, Any] | None:
    """Flat answer for the contract ask, or ``None`` when this path is off."""
    if body.dms_payload is None or not plan_sql_enabled():
        return None
    return plan_sql_answer(
        plan_payload.PlanSqlRequest(body.question, body.dms_payload),
        session_id=body.session_id,
        space_id=body.space_id,
        verified=verified,
        scored_pack_id=body.scored_pack_id,
    )


# -- guards (module level so the guard-removed proofs can switch one off) -------


def _journaled(step: ModelStep, journal: list[freeroute.RouteStamp]) -> bool:
    """True only for a stamp FreeRoute itself appended during this ask."""
    return step.route is not None and any(step.route is seen for seen in journal)


def _gate(sql: str, grant: Any) -> Any:
    from CortexOS.dms.sql_validate_gate import run_gate
    from CortexOS.dms.warehouse_db import load_semantic_layer

    return run_gate(sql, load_semantic_layer(), verified=grant)


def _sql_tables(sql: str) -> set[str]:
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(sql, read="duckdb")
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    return {t.name.lower() for t in tree.find_all(exp.Table) if t.name} - ctes


def _is_scored_round(scored_pack_id: str | None) -> bool:
    from CortexOS.memory.space_memory import get_space_memory

    return get_space_memory().is_scored_round(scored_pack_id)


# -- envelopes ------------------------------------------------------------------


def _served(stamp: StepStamp | None, reason: str) -> dict[str, Any]:
    if stamp is None:
        return {
            "served_provider": None,
            "served_model": None,
            "served_local": False,
            "served_reason": reason,
        }
    return {
        "served_provider": stamp.served_provider,
        "served_model": stamp.served_model,
        "served_local": stamp.served_local,
        "served_reason": stamp.served_reason or None,
    }


def _not_answered(
    question: str,
    audit_id: str,
    steps: list[StepStamp],
    *,
    code: str,
    detail: str,
    refused: bool,
) -> dict[str, Any]:
    reason = f"{code}: {detail}" if detail else code
    route = "refused" if refused else "abstain"
    return {
        "answer": f"Not answered: {freeroute.redact(reason, limit=400)}.",
        "sql_used": None,
        "audit_id": audit_id,
        "route": route,
        "layer": route,
        "badge": route,
        "row_count": 0,
        "rows": [],
        "assumptions": [reason, *(s.line() for s in steps)],
        "plan_sql_steps": [s.public() for s in steps],
        **_served(None, reason),
    }


def _model_refusal(step: ModelStep, journal: list[freeroute.RouteStamp]) -> tuple[str, str, bool]:
    """``(code, detail, refused)`` when this model step cannot be used, else ``('', '', False)``."""
    if step.ok and not _journaled(step, journal):
        return NOT_FREEROUTE, f"{step.kind} step was not served through OpenVault FreeRoute", True
    if not step.ok:
        return MODEL_UNAVAILABLE, f"{step.kind} step: {step.reason}", False
    missing = [
        name
        for name in ("served_provider", "served_model")
        if not str(getattr(step.stamp, name, "") or "").strip()
    ]
    if missing:
        return (
            ROUTE_STAMP_MISSING,
            f"{step.kind} step response omitted {' and '.join(missing)}",
            True,
        )
    return "", "", False


def plan_sql_answer(
    request: plan_payload.PlanSqlRequest,
    *,
    session_id: str | None,
    space_id: str | None,
    verified: Any,
    scored_pack_id: str | None = None,
    generator: PlanSqlGenerator | None = None,
) -> dict[str, Any]:
    """One plan+SQL pass for ``request``. Never widens past the signed grant."""
    from CortexOS.dms.answer_engine import UngroundedSession, resolve_product_grant
    from CortexOS.execution.manifest import ManifestError

    question = request.question
    audit_id = str(uuid.uuid4())
    steps: list[StepStamp] = []

    def stop(code: str, detail: str, *, refused: bool = True) -> dict[str, Any]:
        return _not_answered(
            question, audit_id, steps, code=code, detail=detail, refused=refused
        )

    try:
        grant, _, granted = resolve_product_grant(session_id, verified, space_id=space_id)
    except UngroundedSession as exc:
        steps.append(StepStamp.cortex("grant", "cortex:session-grant", f"refused: {exc}"))
        return stop(UNGROUNDED, str(exc))
    steps.append(
        StepStamp.cortex("grant", "cortex:session-grant", f"tables={sorted(granted)}")
    )

    widened = plan_payload.widening(request, granted)
    if widened:
        steps.append(StepStamp.cortex("payload", "cortex:payload-check", "refused"))
        return stop(widened[0].split(":", 1)[0], ", ".join(widened))
    selected = sorted(request.table_names())
    steps.append(StepStamp.cortex("payload", "cortex:payload-check", f"tables={selected}"))

    gen = generator or default_generator()
    split = freeroute.SPLIT_BENCHMARK if _is_scored_round(scored_pack_id) else None
    with freeroute.journal(split=split) as journal:
        plan = gen.plan(request)
        steps.append(plan.stamp)
        code, detail, refused = _model_refusal(plan, journal)
        if code:
            return stop(code, detail, refused=refused)
        sql_step = gen.sql(request, plan)
        steps.append(sql_step.stamp)
        code, detail, refused = _model_refusal(sql_step, journal)
        if code:
            return stop(code, detail, refused=refused)

    from CortexOS.dms.sql_extract import extract_select

    sql = extract_select(sql_step.text)
    if not sql:
        steps.append(StepStamp.cortex("gate", "cortex:sql-gate", "no SELECT in model text"))
        freeroute.note_verdict(sql_step.route, "gate_fail")
        return stop(NO_SELECT, "model text held no single SELECT")

    gate = _gate(sql, grant)
    if not gate.passed or not (gate.source_sql or gate.safe_sql):
        violations = ", ".join(gate.violations) or "gate failed"
        steps.append(StepStamp.cortex("gate", "cortex:sql-gate", f"refused: {violations}"))
        freeroute.note_verdict(sql_step.route, "gate_fail")
        return stop(MANIFEST_REFUSED if gate.manifest_refused else GATE_REFUSED, violations)
    served_sql = gate.source_sql or gate.safe_sql
    outside = sorted(_sql_tables(served_sql) - set(selected))
    if outside:
        steps.append(StepStamp.cortex("gate", "cortex:sql-gate", f"refused: outside={outside}"))
        freeroute.note_verdict(sql_step.route, "gate_fail")
        return stop(OUTSIDE_PAYLOAD, ", ".join(outside))
    steps.append(StepStamp.cortex("gate", "cortex:sql-gate", "passed run_gate under the grant"))
    freeroute.note_verdict(sql_step.route, "gate_pass")

    from CortexOS.dms.sql_validate_gate import SqlGateAbstain
    from CortexOS.execution.submit import execute_sql

    try:
        rows, _, _ = execute_sql(grant, served_sql)
    except ManifestError as exc:
        steps.append(StepStamp.cortex("execute", "cortex:execute_sql", f"refused: {exc.code}"))
        return stop(MANIFEST_REFUSED, f"{type(exc).__name__}:{exc.code}")
    except SqlGateAbstain as exc:
        steps.append(StepStamp.cortex("execute", "cortex:execute_sql", "refused: EXPLAIN"))
        return stop(GATE_REFUSED, str(exc))
    except Exception as exc:  # noqa: BLE001 - an executor fault is an abstain, never a 500
        steps.append(StepStamp.cortex("execute", "cortex:execute_sql", type(exc).__name__))
        return stop(EXECUTE_FAILED, type(exc).__name__, refused=False)
    steps.append(StepStamp.cortex("execute", "cortex:execute_sql", f"rows={len(rows)}"))
    if not rows:
        return stop(EMPTY_RESULT, "the gated SQL matched no rows", refused=False)

    from CortexOS.dms.query_service import synthesize_answer

    call = sql_step.stamp.served_call_id
    return {
        "answer": synthesize_answer(rows, question),
        "sql_used": served_sql,
        "audit_id": audit_id,
        "route": LAYER,
        "layer": LAYER,
        "badge": "session",
        "row_count": len(rows),
        "rows": rows,
        "granted_sources": sorted(_sql_tables(served_sql)),
        "assumptions": [
            *(f"plan: {line}" for line in plan.plan),
            *(s.line() for s in steps),
            CAVEAT,
        ],
        "plan_sql_steps": [s.public() for s in steps],
        "provenance": {
            "layer": LAYER,
            "badge": "session",
            "query_source": f"openvault-freeroute:{call}" if call else "openvault-freeroute",
            "assumptions": CAVEAT,
        },
        **_served(sql_step.stamp, ""),
    }


__all__ = [
    "ENABLED_ENV",
    "LAYER",
    "default_generator",
    "plan_sql_answer",
    "plan_sql_enabled",
    "try_plan_sql",
]
