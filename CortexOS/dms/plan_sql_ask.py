"""C-LOOP-A (#303) ask seam: DMS payload -> plan -> SQL -> gate -> execute.

Off unless ``CORTEX_PLAN_SQL=1`` and the ask carries ``dms_payload``; with the
flag off ``/v1/contract/ask`` never enters this module's answer path.

Order, each step stamped (``StepStamp``):

1. ``grant``: the signed session grant, resolved the same way the contract ask
   does (``resolve_product_grant``). Unbound, expired, self-issued or wrong-Space
   grants are refused.
2. ``payload``: every selected table and both ends of every join must be in the
   grant. A payload that widens the grant is refused before any model call.
3. ``sample``: distinct values from the granted payload columns, so filters can
   be grounded in what the tables actually hold. No model call.
4. ``plan`` / ``sql``: model calls through the injected ``PlanSqlGenerator``
   (default: OpenVault FreeRoute). A step whose route stamp FreeRoute did not
   journal, or whose response omitted ``served_provider`` / ``served_model``,
   is refused.
5. On a SQL, execution, empty, or implausible result that is not an ungranted
   table and not destructive SQL: feed the error back and regenerate, up to
   ``SQL_ATTEMPTS`` writes on the same model.
6. ``escalate``: one more plan+SQL pass on the stronger route OpenVault
   configured (``routes.stronger``). Cortex does not pick a provider. If OV
   did not configure one, the ladder reconfirms and does not invent a model.
7. ``reconfirm``: why this would abstain, plus the closest answerable question.
   ``plan_sql_confirm=yes`` runs that question. ``no`` answers
   ``not found in the database``.

The answer that leaves this path stamps the rung that produced it
(``plan``, ``error-fed-retry-<N>``, ``stronger-model``, ``reconfirm``) on
``plan_sql_rung`` and on an assumptions line ``rung: <token>``. A direct
abstain and a confirm-no do not carry a rung.

Direct abstain is only an ungranted table or destructive SQL (and a grant or
route-stamp failure, which never reaches a model result). PII is not an abstain
case and this path does not mask it.

Scored rounds (``scored_pack_id`` / ``CORTEX_SCORED_ROUND``, the #290 guard) run
FreeRoute on the benchmark split so the route store never learns from them.
This path writes nothing to per-Space memory.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Sequence
from dataclasses import replace
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
RECONFIRM = "reconfirm"

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
IMPLAUSIBLE_RESULT = "PLAN_SQL_IMPLAUSIBLE_RESULT"

NOT_FOUND_ANSWER = "not found in the database"

# Same-model writes (the first, plus error-fed regenerations). The stronger
# pass gets one write: the failures are already in its prompt.
SQL_ATTEMPTS = 2
STRONG_SQL_ATTEMPTS = 1
RUNG_PLAN = "plan"
RUNG_STRONG = "stronger-model"
RUNG_RECONFIRM = "reconfirm"

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAMPLE_ROWS = 8
_MAX_SAMPLE_COLUMNS = 12
# Statement class from the extractor or the gate. Not a scan of the question.
_DESTRUCTIVE_KINDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "ATTACH",
    "COPY",
)

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
        confirm=body.plan_sql_confirm,
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


def rung_retry(n: int) -> str:
    """Token for the Nth error-fed SQL rewrite on the first model. N starts at 1."""
    return f"error-fed-retry-{n}"


def _mark_rung(steps: list[StepStamp], rung: str) -> None:
    steps.append(StepStamp.cortex("rung", "cortex:plan-sql-rung", rung))


def _last_served_stamp(steps: list[StepStamp]) -> StepStamp | None:
    """Last step that copied served model and provider from an OpenVault response."""
    for step in reversed(steps):
        if step.served_provider or step.served_model:
            return step
    return None


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
        "reconfirm": None,
        "plan_sql_rung": None,
        **_served(None, reason),
    }


def _not_found(audit_id: str, steps: list[StepStamp]) -> dict[str, Any]:
    note = "user answered no to the closest question"
    return {
        "answer": NOT_FOUND_ANSWER,
        "sql_used": None,
        "audit_id": audit_id,
        "route": LAYER,
        "layer": LAYER,
        "badge": "session",
        "row_count": 0,
        "rows": [],
        "assumptions": [note, *(s.line() for s in steps)],
        "reconfirm": None,
        "plan_sql_rung": None,
        "provenance": {"layer": LAYER, "badge": "session", "assumptions": note},
        **_served(None, note),
    }


def _reconfirm_envelope(
    audit_id: str,
    steps: list[StepStamp],
    *,
    why: str,
    closest: str,
    stamp: StepStamp | None,
    rung: str,
) -> dict[str, Any]:
    answer = (
        f"Reconfirm: {why} Closest question: {closest} "
        f"Yes runs that question. No answers '{NOT_FOUND_ANSWER}'."
    )
    call = stamp.served_call_id if stamp is not None else ""
    return {
        "answer": answer,
        "sql_used": None,
        "audit_id": audit_id,
        "route": RECONFIRM,
        "layer": RECONFIRM,
        "badge": RECONFIRM,
        "row_count": 0,
        "rows": [],
        "assumptions": [why, *(s.line() for s in steps), f"rung: {rung}"],
        "reconfirm": {"why": why, "closest_question": closest},
        "plan_sql_rung": rung,
        "provenance": {
            "layer": RECONFIRM,
            "badge": RECONFIRM,
            "query_source": f"openvault-freeroute:{call}" if call else "openvault-freeroute",
            "assumptions": why,
        },
        **_served(stamp, why),
    }


def _success(
    question: str,
    audit_id: str,
    steps: list[StepStamp],
    *,
    plan: ModelStep,
    sql_step: ModelStep,
    rows: list[dict[str, Any]],
    served_sql: str,
    rung: str,
) -> dict[str, Any]:
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
            f"rung: {rung}",
            CAVEAT,
        ],
        "plan_sql_steps": [s.public() for s in steps],
        "reconfirm": None,
        "plan_sql_rung": rung,
        "provenance": {
            "layer": LAYER,
            "badge": "session",
            "query_source": f"openvault-freeroute:{call}" if call else "openvault-freeroute",
            "assumptions": CAVEAT,
        },
        **_served(sql_step.stamp, ""),
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


def _remember(step: ModelStep, failed: set[str]) -> None:
    served = (step.stamp.served_model or "").strip()
    if served:
        failed.add(served)
    if step.route is not None and (step.route.requested or "").strip():
        failed.add(step.route.requested.strip())


def _security_stop(step: ModelStep, journal: list[freeroute.RouteStamp]) -> tuple[str, str] | None:
    """A trust failure, or a model call that was never sent. Else None.

    A missing OpenVault stronger route is not a trust failure: the ladder
    reconfirms instead of inventing a provider.
    """
    reason = step.reason or ""
    # A missing stronger route, or OpenVault saying the pin is outside its
    # own catalog, is not a direct abstain. The ladder reconfirms.
    if freeroute.NO_STRONGER_ROUTE in reason or freeroute.MODEL_NOT_ALLOWLISTED in reason:
        return None
    code, detail, refused = _model_refusal(step, journal)
    if code and (refused or step.route is None):
        return code, detail
    return None


def _destructive_reason(reason: str) -> bool:
    upper = f" {reason.upper()} "
    if any(tok in upper for tok in ("DDL_ATTEMPT", "MULTI_STATEMENT", "STATEMENT_NOT_ALLOWED")):
        return True
    return any(
        f" {kind} " in upper or f"({kind}" in upper or f"{kind} REFUSED" in upper
        for kind in _DESTRUCTIVE_KINDS
    )


def _ungranted(sql: str, granted: set[str]) -> bool:
    try:
        tables = _sql_tables(sql)
    except Exception:  # noqa: BLE001 - unreadable SQL is not proof of a grant
        return False
    return bool(tables - granted)


def _missing_literals(sql: str, request: plan_payload.PlanSqlRequest) -> list[str]:
    """Comparison literals that are not in the sampled values for that column."""
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001
        return []
    missed: list[str] = []
    for eq in tree.find_all(exp.EQ):
        left, right = eq.left, eq.right
        if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
            col, lit = left, right
        elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
            col, lit = right, left
        else:
            continue
        if not lit.is_string:
            continue
        pool: list[str] = []
        for cols in request.column_values.values():
            pool.extend(cols.get(col.name, ()))
        if pool and str(lit.this) not in pool:
            missed.append(f"{col.name}={str(lit.this)!r} sampled={pool[:8]}")
    return missed


def _sample_values(
    grant: Any, request: plan_payload.PlanSqlRequest
) -> tuple[dict[str, dict[str, tuple[str, ...]]], str]:
    """Distinct values for payload columns the grant already allows. Never raises."""
    from CortexOS.execution.submit import execute_sql

    found: dict[str, dict[str, tuple[str, ...]]] = {}
    taken = 0
    errors = 0
    for table in request.payload.tables:
        if not _IDENT.fullmatch(table.name):
            continue
        cols: dict[str, tuple[str, ...]] = {}
        for column in table.columns:
            if taken >= _MAX_SAMPLE_COLUMNS or not _IDENT.fullmatch(column.name):
                continue
            taken += 1
            sql = (
                f'SELECT DISTINCT "{column.name}" AS v FROM "{table.name}" '
                f'WHERE "{column.name}" IS NOT NULL LIMIT {_SAMPLE_ROWS}'
            )
            try:
                rows, _, _ = execute_sql(grant, sql)
            except Exception:  # noqa: BLE001 - a sample miss is not an abstain
                errors += 1
                continue
            vals: list[str] = []
            for row in rows:
                value = row.get("v")
                if value is None:
                    continue
                vals.append(str(value)[:80])
                if len(vals) >= _SAMPLE_ROWS:
                    break
            if vals:
                cols[column.name] = tuple(vals)
        if cols:
            found[table.name] = cols
    nvals = sum(len(v) for cols in found.values() for v in cols.values())
    return found, f"columns={taken} values={nvals} errors={errors}"


class _RouteRefusal:
    """A stronger rung that must not call a model. No provider is chosen."""

    def __init__(self, reason: str) -> None:
        self.pin = ""
        self._reason = reason

    def _no(self, kind: str) -> ModelStep:
        stamp = StepStamp.cortex(kind, "openvault-freeroute", self._reason)
        return ModelStep(kind, False, "", self._reason, stamp, None)

    def plan(self, request: plan_payload.PlanSqlRequest) -> ModelStep:
        del request
        return self._no("plan-strong")

    def sql(
        self,
        request: plan_payload.PlanSqlRequest,
        plan: ModelStep,
        *,
        prior_violations: Sequence[str] = (),
    ) -> ModelStep:
        del request, plan, prior_violations
        return self._no("sql-strong")

    def reconfirm(
        self, request: plan_payload.PlanSqlRequest, failures: Sequence[str]
    ) -> ModelStep:
        del request, failures
        return self._no("reconfirm-strong")


def _stronger_generator(failed: set[str]) -> FreeRoutePlanSqlGenerator | _RouteRefusal:
    """OpenVault's configured stronger route. Cortex does not pick a provider.

    The id is whatever OpenVault put on ``routes.stronger``. It is not
    checked against a list of model names. If that id is outside OpenVault's
    own catalog, OpenVault's response says so and the ladder reconfirms.
    """
    del failed
    pin = ""
    try:
        arm = freeroute.arming()
        pin = freeroute.configured_stronger_route(arm)
    except Exception:  # noqa: BLE001 - no status still does not invent a model
        pin = ""
    if not pin:
        return _RouteRefusal(
            f"{freeroute.NO_STRONGER_ROUTE}: OpenVault status has no routes.stronger"
        )
    return FreeRoutePlanSqlGenerator(
        pin=pin,
        pin_source="openvault-stronger-route",
        tier="strong",
    )


def _not_served(step: ModelStep) -> str:
    """Requested id OpenVault did not serve. Empty for ``auto`` and for a match."""
    route = step.route
    if route is None or not step.ok:
        return ""
    # A catalogue pick is not a pin. Only an id Cortex asked OV to serve
    # (the stronger route, or an operator pin) is a promise OV can miss.
    source = f"{route.source} {route.route_source}"
    if "pin" not in source:
        return ""
    requested = (route.requested or "").strip()
    if not requested or requested == "auto":
        return ""
    served = (route.served or step.stamp.served_model or "").strip()
    if freeroute.request_was_served(requested, served):
        return ""
    return requested


def _fallback_closest(request: plan_payload.PlanSqlRequest) -> str:
    for table, cols in request.column_values.items():
        for col, vals in cols.items():
            if vals:
                return f"What are the distinct {col} values in {table}?"
    return f"How many rows are in {request.payload.tables[0].name}?"


def _parse_reconfirm(
    text: str, failures: Sequence[str], request: plan_payload.PlanSqlRequest
) -> tuple[str, str]:
    why, closest = "", ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        upper = line.upper()
        if upper.startswith("WHY:"):
            why = line.split(":", 1)[1].strip()
        elif upper.startswith("CLOSEST:"):
            closest = line.split(":", 1)[1].strip()
    if not why:
        why = failures[-1] if failures else "the query could not be grounded"
    if not closest:
        closest = _fallback_closest(request)
    return why[:400], closest[:300]


def _direct_gate(sql: str, gate: Any, granted: set[str]) -> str | None:
    """Refusal code when this gate failure must not be retried, else None."""
    blob = " ".join(gate.violations)
    if _destructive_reason(blob) or "path_not_allowed" in blob or _ungranted(sql, granted):
        return MANIFEST_REFUSED if gate.manifest_refused else GATE_REFUSED
    return None


def plan_sql_answer(
    request: plan_payload.PlanSqlRequest,
    *,
    session_id: str | None,
    space_id: str | None,
    verified: Any,
    scored_pack_id: str | None = None,
    generator: PlanSqlGenerator | None = None,
    confirm: str | None = None,
) -> dict[str, Any]:
    """Plan, then SQL, with grounded retries. Never widens past the signed grant."""
    from CortexOS.dms.answer_engine import UngroundedSession, resolve_product_grant

    question = request.question
    audit_id = str(uuid.uuid4())
    steps: list[StepStamp] = []
    failed_models: set[str] = set()
    failures: list[str] = []

    def stop(code: str, detail: str, *, refused: bool = True) -> dict[str, Any]:
        return _not_answered(
            question, audit_id, steps, code=code, detail=detail, refused=refused
        )

    try:
        grant, _, granted_list = resolve_product_grant(
            session_id, verified, space_id=space_id
        )
    except UngroundedSession as exc:
        steps.append(StepStamp.cortex("grant", "cortex:session-grant", f"refused: {exc}"))
        return stop(UNGROUNDED, str(exc))
    granted = {name.lower() for name in granted_list}
    steps.append(
        StepStamp.cortex("grant", "cortex:session-grant", f"tables={sorted(granted)}")
    )

    widened = plan_payload.widening(request, granted)
    if widened:
        steps.append(StepStamp.cortex("payload", "cortex:payload-check", "refused"))
        return stop(widened[0].split(":", 1)[0], ", ".join(widened))
    selected = set(request.table_names())
    steps.append(
        StepStamp.cortex("payload", "cortex:payload-check", f"tables={sorted(selected)}")
    )

    if confirm == "no":
        steps.append(StepStamp.cortex("confirm", "cortex:plan-sql-confirm", "no"))
        return _not_found(audit_id, steps)

    sampled, sample_reason = _sample_values(grant, request)
    steps.append(StepStamp.cortex("sample", "cortex:column-values", sample_reason))
    request = replace(request, column_values=sampled)
    if confirm == "yes":
        steps.append(StepStamp.cortex("confirm", "cortex:plan-sql-confirm", "yes"))

    gen = generator or default_generator()

    def _round(
        active: PlanSqlGenerator,
        attempts: int,
        rung_of,
    ) -> dict[str, Any] | None:
        plan = active.plan(request)
        steps.append(plan.stamp)
        _remember(plan, failed_models)
        security = _security_stop(plan, journal)
        if security:
            return stop(security[0], security[1], refused=security[0] != MODEL_UNAVAILABLE)
        if not plan.ok:
            failures.append(f"{MODEL_UNAVAILABLE}: {plan.kind} step: {plan.reason}")
            return None
        missed = _not_served(plan)
        if missed:
            failures.append(f"{freeroute.MODEL_NOT_SERVED}: {missed}")
            return None
        priors = list(failures)
        for attempt in range(attempts):
            sql_step = active.sql(request, plan, prior_violations=priors)
            steps.append(sql_step.stamp)
            _remember(sql_step, failed_models)
            security = _security_stop(sql_step, journal)
            if security:
                return stop(security[0], security[1], refused=security[0] != MODEL_UNAVAILABLE)
            if not sql_step.ok:
                detail = f"{MODEL_UNAVAILABLE}: {sql_step.kind} step: {sql_step.reason}"
                priors.append(detail)
                failures.append(detail)
                continue
            missed = _not_served(sql_step)
            if missed:
                detail = f"{freeroute.MODEL_NOT_SERVED}: {missed}"
                priors.append(detail)
                failures.append(detail)
                continue
            outcome = _use_sql(sql_step, plan, priors, rung_of(attempt))
            if outcome is not None:
                return outcome
        return None

    def _use_sql(
        sql_step: ModelStep, plan: ModelStep, priors: list[str], rung: str
    ) -> dict[str, Any] | None:
        """A final envelope, or None when ``priors`` gained a retryable error."""
        from CortexOS.dms.sql_extract import extract_statement

        extracted = extract_statement(sql_step.text)
        if not extracted.sql:
            reason = extracted.reason or "model text held no single SELECT"
            if _destructive_reason(reason):
                steps.append(StepStamp.cortex("gate", "cortex:sql-gate", f"refused: {reason}"))
                freeroute.note_verdict(sql_step.route, "gate_fail")
                return stop(NO_SELECT, reason)
            detail = f"{NO_SELECT}: {reason}"
            priors.append(detail)
            failures.append(detail)
            steps.append(StepStamp.cortex("gate", "cortex:sql-gate", detail))
            freeroute.note_verdict(sql_step.route, "gate_fail")
            return None

        sql = extracted.sql
        gate = _gate(sql, grant)
        if not gate.passed or not (gate.source_sql or gate.safe_sql):
            violations = ", ".join(gate.violations) or "gate failed"
            code = _direct_gate(sql, gate, granted)
            steps.append(StepStamp.cortex("gate", "cortex:sql-gate", f"refused: {violations}"))
            freeroute.note_verdict(sql_step.route, "gate_fail")
            if code:
                return stop(code, violations)
            detail = f"{GATE_REFUSED}: {violations}"
            priors.append(detail)
            failures.append(detail)
            return None

        served_sql = gate.source_sql or gate.safe_sql
        try:
            tables = _sql_tables(served_sql)
        except Exception as exc:  # noqa: BLE001
            detail = f"{GATE_REFUSED}: SQL tables unreadable ({type(exc).__name__})"
            priors.append(detail)
            failures.append(detail)
            return None
        outside = sorted(tables - selected)
        if outside:
            detail = f"{OUTSIDE_PAYLOAD}: {', '.join(outside)}"
            steps.append(StepStamp.cortex("gate", "cortex:sql-gate", f"refused: {detail}"))
            freeroute.note_verdict(sql_step.route, "gate_fail")
            # Granted but not selected: retry, do not execute. An ungranted table
            # is a gate refusal (_direct_gate) and never reaches this branch.
            priors.append(detail)
            failures.append(detail)
            return None
        steps.append(StepStamp.cortex("gate", "cortex:sql-gate", "passed run_gate under the grant"))
        freeroute.note_verdict(sql_step.route, "gate_pass")
        return _execute(sql_step, plan, served_sql, priors, rung)

    def _execute(
        sql_step: ModelStep,
        plan: ModelStep,
        served_sql: str,
        priors: list[str],
        rung: str,
    ) -> dict[str, Any] | None:
        from CortexOS.dms.sql_validate_gate import SqlGateAbstain
        from CortexOS.execution.manifest import ManifestError
        from CortexOS.execution.submit import execute_sql

        try:
            rows, _, _ = execute_sql(grant, served_sql)
        except ManifestError as exc:
            steps.append(StepStamp.cortex("execute", "cortex:execute_sql", f"refused: {exc.code}"))
            if exc.code in {"statement_not_allowed", "path_not_allowed"} or _ungranted(
                served_sql, granted
            ):
                code = MANIFEST_REFUSED
                return stop(code, f"{type(exc).__name__}:{exc.code}")
            detail = f"{EXECUTE_FAILED}: {type(exc).__name__}:{exc.code}"
            priors.append(detail)
            failures.append(detail)
            return None
        except SqlGateAbstain as exc:
            steps.append(StepStamp.cortex("execute", "cortex:execute_sql", "refused: EXPLAIN"))
            detail = f"{GATE_REFUSED}: {exc}"
            priors.append(detail)
            failures.append(detail)
            return None
        except Exception as exc:  # noqa: BLE001 - an executor fault is retried, never a 500
            steps.append(StepStamp.cortex("execute", "cortex:execute_sql", type(exc).__name__))
            detail = f"{EXECUTE_FAILED}: {type(exc).__name__}"
            priors.append(detail)
            failures.append(detail)
            return None

        if not rows:
            steps.append(StepStamp.cortex("execute", "cortex:execute_sql", "rows=0"))
            extra = _missing_literals(served_sql, request)
            detail = f"{EMPTY_RESULT}: the gated SQL matched no rows"
            if extra:
                detail += "; filter values not in sampled column values: " + "; ".join(extra[:4])
            priors.append(detail)
            failures.append(detail)
            return None
        if all(all(value is None for value in row.values()) for row in rows):
            steps.append(StepStamp.cortex("execute", "cortex:execute_sql", "implausible: all null"))
            detail = f"{IMPLAUSIBLE_RESULT}: every returned value is null"
            priors.append(detail)
            failures.append(detail)
            return None
        steps.append(StepStamp.cortex("execute", "cortex:execute_sql", f"rows={len(rows)}"))
        _mark_rung(steps, rung)
        return _success(
            question,
            audit_id,
            steps,
            plan=plan,
            sql_step=sql_step,
            rows=rows,
            served_sql=served_sql,
            rung=rung,
        )

    split = freeroute.SPLIT_BENCHMARK if _is_scored_round(scored_pack_id) else None
    with freeroute.journal(split=split) as journal:
        answered = _round(
            gen, SQL_ATTEMPTS, lambda n: RUNG_PLAN if n == 0 else rung_retry(n)
        )
        if answered is not None:
            return answered
        strong = _stronger_generator(failed_models)
        steps.append(
            StepStamp.cortex(
                "escalate",
                "openvault-freeroute",
                f"stronger route={strong.pin or 'unset'}",
            )
        )
        request = replace(request, prior_failures=tuple(failures)[:8])
        answered = _round(strong, STRONG_SQL_ATTEMPTS, lambda _n: RUNG_STRONG)
        if answered is not None:
            return answered
        policy = next(
            (item for item in reversed(failures) if freeroute.MODEL_NOT_ALLOWLISTED in item),
            "",
        )
        if policy:
            why, closest = _parse_reconfirm("", failures, request)
            served = _last_served_stamp(steps)
            _mark_rung(steps, RUNG_RECONFIRM)
            return _reconfirm_envelope(
                audit_id,
                steps,
                why=why,
                closest=closest,
                stamp=served,
                rung=RUNG_RECONFIRM,
            )
        rec = strong.reconfirm(request, tuple(failures)[:8])
        steps.append(rec.stamp)
        _remember(rec, failed_models)
        security = _security_stop(rec, journal)
        if security and security[0] != MODEL_UNAVAILABLE:
            return stop(security[0], security[1], refused=True)
        missed = _not_served(rec)
        if missed:
            failures.append(f"{freeroute.MODEL_NOT_SERVED}: {missed}")
        why, closest = _parse_reconfirm(
            rec.text if rec.ok and not missed else "", failures, request
        )
        _mark_rung(steps, RUNG_RECONFIRM)
        return _reconfirm_envelope(
            audit_id,
            steps,
            why=why,
            closest=closest,
            stamp=rec.stamp if rec.ok else None,
            rung=RUNG_RECONFIRM,
        )


__all__ = [
    "EMPTY_RESULT",
    "ENABLED_ENV",
    "IMPLAUSIBLE_RESULT",
    "LAYER",
    "NOT_FOUND_ANSWER",
    "RECONFIRM",
    "RUNG_PLAN",
    "RUNG_RECONFIRM",
    "RUNG_STRONG",
    "SQL_ATTEMPTS",
    "rung_retry",
    "default_generator",
    "plan_sql_answer",
    "plan_sql_enabled",
    "try_plan_sql",
]
