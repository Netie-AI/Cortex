"""C-LOOP-A (#303) ask seam: DMS payload -> plan -> SQL -> gate -> execute.

Off unless ``CORTEX_PLAN_SQL=1`` and the ask carries ``dms_payload``; with the
flag off ``/v1/contract/ask`` never enters this module's answer path.

Order, each step stamped (``StepStamp``):

1. ``grant``: the signed session grant, resolved the same way the contract ask
   does (``resolve_product_grant``). Unbound, expired, self-issued or wrong-Space
   grants are refused.
2. ``payload``: every selected table and both ends of every join must be in the
   grant. A payload that widens the grant is refused before any model call.
3. ``sample``: distinct values from the granted payload columns DMS marked
   ``masked: false`` (contract 1.9.0), so filters can be grounded in what the
   tables actually hold. A column marked masked, or with no mask state, keeps
   its name and type in the prompt and is never probed. Off unless
   ``CORTEX_GRANTED_CATALOG`` is on (see below). At most 8 distinct values a
   column, each stripped of control and bidi characters, cut to 64
   characters and quoted as a SQL string literal; the block counts inside
   #351's 8192-byte schema cap. Values go only to the OpenVault FreeRoute
   generator, and steps record counts, never a value. No model call.
4. ``sql``: one model call per attempt through the injected ``PlanSqlGenerator``
   (default: OpenVault FreeRoute). The #329 planner call is folded into this
   call: there is no serial planner or classifier before generation. A step
   whose route stamp FreeRoute did not journal, or whose response omitted
   ``served_provider`` / ``served_model``, is refused. The prompt is the
   granted catalog intersected with ``schema_context`` when DMS sent one.
5. On a SQL, execution, empty, or implausible result that is not an ungranted
   table and not destructive SQL: feed the error back and regenerate, up to
   ``SQL_ATTEMPTS`` writes on the same model. SQL with an output column that
   does not derive from a granted table column (a literal, a VALUES list) is
   such an error: it is fed back and never executed (``sql_lineage``). An
   unknown-column error also carries a did-you-mean hint when a granted
   catalog column is a typo of the unknown name (one edit). A column that is
   not close is never offered, so an absent column gets no hint.
6. ``escalate``: one more SQL pass (still one call) on the stronger route
   OpenVault configured (``routes.stronger``). Cortex does not pick a provider.
   If OV did not configure one, the ladder reconfirms and does not invent a
   model.
7. ``reconfirm``: why this would abstain, plus the closest answerable question.
   ``plan_sql_confirm=yes`` runs that question. ``no`` answers
   ``not found in the database`` as an abstain (never a session badge) and
   logs a pipeline-failure ticket on the F1 audit ledger (``CortexOS.audit``).

With ``CORTEX_PLAN_SQL_VOTE=1`` a SQL that ran with rows is a candidate, not an
answer: it is served only when a second, independent SQL returns the same rows
(``plan_sql_vote``). A split serves nothing and goes straight to step 7. Vote
calls count under the rung that ran them, the stronger route's under
``stronger-model``. With the plan folded (step 4) neither reading carries a
plan, so the second reading has its own framing
(``payload.SECOND_READING``): a different prompt from the same granted
catalog and question. It runs only because its prompt differs: a second
reading whose prompt would hash (``payload.prompt_sha256``) to one the first
reading sent, or that cannot be shown to differ, is skipped and the vote
relies on the stronger route.

The answer that leaves this path stamps the rung that produced it
(``plan``, ``error-fed-retry-<N>``, ``stronger-model``, ``reconfirm``) on
``plan_sql_rung`` and on an assumptions line ``rung: <token>``. A direct
abstain and a confirm-no do not carry a rung.

``Answer.model_calls`` counts logical FreeRoute ``complete()`` calls on those
same steps, plus ``total``: one per call the ladder issues. A transport or
proxy retry under that call (an upstream 429 retried by the cap proxy or the
FreeRoute client) is not a step. DMS reads the per-step counts only from that
object.

``CORTEX_GRANTED_CATALOG`` (default off, separate from ``CORTEX_PLAN_SQL``)
switches on both ``AskRequest.schema_context`` on this path and step 3. Off,
the contract ask ignores ``schema_context`` past its declared type and probes
no value: the answer is the one the same ask gets without that field. It
stays off until DMS sends ``masked: false`` (dms#456).

``CORTEX_PLAN_SQL_READINGS`` (default off, needs the vote on; contract 1.10.0,
founder ruling 2026-10-29 on split readings): a split's reconfirm may offer up
to three of the disagreeing readings (``reconfirm.readings``), each a
statement Cortex writes from the gated SQL with every literal and comment
removed (``plan_sql.describe``) and a row count, never a value. Only readings
whose step passed the FreeRoute check in this ask are held
(``plan_sql.readings``), without rows. ``AskRequest.plan_sql_reading_id``
picks one: the held model text runs again through extract, the SQL gate under
the current grant, the payload scope, lineage, literal grounding, the
manifest-enforcing execute and the empty and implausible checks, with no
model call and no sampling. A pick that no longer runs reconfirms; it is never
served unless every gate passes. Every 200 response to a pick echoes the id.

Direct abstain is only an ungranted table or destructive SQL (and a grant or
route-stamp failure, which never reaches a model result). PII wording in a
question is not an abstain case and this path does not mask answer rows. What
it masks is the prompt: no sampled value of a column DMS did not mark unmasked
reaches a model (step 3).

Scored rounds (``scored_pack_id`` / ``CORTEX_SCORED_ROUND``, the #290 guard) run
FreeRoute on the benchmark split so the route store never learns from them.
This path writes nothing to per-Space memory.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from cortex_contract.answer import AskRequest, Badge

from CortexOS.dms import plan_sql_vote
from CortexOS.identifier_fold import fold_identifier
from CortexOS.integrations import freeroute
from CortexOS.plan_sql import catalog as plan_catalog
from CortexOS.plan_sql import describe as plan_describe
from CortexOS.plan_sql import payload as plan_payload
from CortexOS.plan_sql import readings as plan_readings
from CortexOS.plan_sql.generator import (
    FreeRoutePlanSqlGenerator,
    ModelStep,
    PlanSqlGenerator,
    StepStamp,
)

_LOG = logging.getLogger(__name__)

ENABLED_ENV = "CORTEX_PLAN_SQL"
#: Default off. On: the contract ask reads ``schema_context`` and step 3
#: samples ``masked: false`` columns. Lead / Security ruling on #386: off
#: until dms#456. Not implied by ``CORTEX_PLAN_SQL``.
CATALOG_ENV = "CORTEX_GRANTED_CATALOG"
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
NOT_FROM_TABLE = "PLAN_SQL_OUTPUT_NOT_FROM_TABLE"
LITERAL_UNGROUNDED = "PLAN_SQL_LITERAL_UNGROUNDED"

NOT_FOUND_ANSWER = "not found in the database"
# Confirm-no is an abstain on the wire: the contract's ABSTAIN token, which
# contract_routes._FLAT_BADGE maps to Badge.ABSTAIN. Never "session".
ABSTAIN = Badge.ABSTAIN.value

# CLAUDE.md section 9: No answers "not found in the database" and logs a
# pipeline-failure ticket. The ticket is one entry on the F1 audit ledger
# (the one ledger, reached through CortexOS.audit). There is no other store.
PIPELINE_FAILURE_EVENT = "plan_sql.pipeline_failure"
TICKET_ACTOR = "cortex.plan_sql"
DECLINED_REASON = "user declined the closest question"
NO_CLOSEST_REASON = "no closest question could be confirmed answerable"

# Same-model writes (the first, plus error-fed regenerations). The stronger
# pass gets one write: the failures are already in its prompt.
# Each write is one FreeRoute complete(). The planner is not a second call.
SQL_ATTEMPTS = 2
MODEL_CALLS_PER_ATTEMPT = 1
STRONG_SQL_ATTEMPTS = 1
RUNG_PLAN = "plan"
RUNG_STRONG = "stronger-model"
RUNG_RECONFIRM = "reconfirm"

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAMPLE_ROWS = plan_payload.SAMPLE_VALUES_PER_COLUMN
_MAX_SAMPLE_COLUMNS = 12
SAMPLE_OFF = "off"
SAMPLE_NOT_OPENVAULT = "not sampled: the generator is not OpenVault FreeRoute"
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

# The vote's readings carry no plan; the second one also has its own framing.
_NO_PLAN = ModelStep("plan", True, "", "", StepStamp.cortex("plan", "cortex:plan-sql-vote", ""))
# A SQL that ran with rows: the model step that wrote it, the gated SQL, the rows.
_Reading = tuple[ModelStep, str, list[dict[str, Any]]]

# PICK-READING-01: a pick replays a held reading; no model plans it.
RUNG_PICK_PLAN = ModelStep(
    "plan", True, "", "", StepStamp.cortex("plan", "cortex:plan-sql-pick", "held reading; no model call")
)
READING_UNAVAILABLE = "plan_sql_reading_unavailable"
READING_UNAVAILABLE_MESSAGE = (
    "picking a reading is not available on this engine; ask the question again"
)


class ReadingUnbound(Exception):
    """A pick names no reading this ask may run. One message, whatever failed."""

    code = "plan_sql_reading_unbound"

    def __init__(self) -> None:
        super().__init__("this reading is no longer available; ask the question again")


CAVEAT = (
    "Plan and SQL are model-generated through OpenVault FreeRoute; the SQL passed "
    "the Cortex SQL gate and ran under this session's grant. Not validated for accuracy."
)


def plan_sql_enabled() -> bool:
    return (os.environ.get(ENABLED_ENV) or "").strip().lower() in {"1", "true", "yes"}


def granted_catalog_enabled() -> bool:
    """``CORTEX_GRANTED_CATALOG``: schema_context on the ask, and value sampling. Default off."""
    return (os.environ.get(CATALOG_ENV) or "").strip().lower() in {"1", "true", "yes"}


def readings_on() -> bool:
    """Split readings are offered and picks accepted: plan+SQL, vote and readings all on."""
    return (
        plan_sql_enabled()
        and plan_sql_vote.vote_enabled()
        and plan_readings.readings_enabled()
    )


def _display_literal(question: str) -> Callable[[str], bool]:
    """Which literals of the gated SQL a statement may print. Display only.

    Default (no founder ruling on question-named filter literals): none. The
    statement then shows no literal but the LIMIT row cap. This never writes,
    picks, gates or routes SQL, and never changes what a pick runs.
    """
    del question
    return lambda _text: False


def _openvault_routed(generator: object) -> bool:
    """True only for the stock FreeRoute generator, whose one egress is OpenVault.

    Exact class: an injected generator, or a subclass that could override
    the call, never receives a sampled value.
    """
    return type(generator) is FreeRoutePlanSqlGenerator


def default_generator() -> PlanSqlGenerator:
    return FreeRoutePlanSqlGenerator()


def try_plan_sql(body: AskRequest, *, verified: Any) -> dict[str, Any] | None:
    """Flat answer for the contract ask, or ``None`` when this path is off.

    ``body.schema_context`` (contract 1.9.0) narrows the prompt to the grant
    intersected with that string. Absent or blank, the payload is the prompt.
    With ``CORTEX_GRANTED_CATALOG`` off it is not read.
    """
    if body.dms_payload is None or not plan_sql_enabled():
        return None
    schema_context = body.schema_context if granted_catalog_enabled() else None
    reading_id = body.plan_sql_reading_id
    env = plan_sql_answer(
        plan_payload.PlanSqlRequest(
            body.question, body.dms_payload, schema_context=schema_context
        ),
        session_id=body.session_id,
        space_id=body.space_id,
        verified=verified,
        scored_pack_id=body.scored_pack_id,
        confirm=body.plan_sql_confirm,
        reading_id=reading_id,
    )
    if reading_id is not None:
        # Contract 1.10.0: every 200 response to a pick carries the id it ran,
        # served, re-gated into a reconfirm, or abstained. Absent otherwise.
        env["plan_sql_reading_id"] = reading_id
    return env


# -- guards (module level so the guard-removed proofs can switch one off) -------


def _journaled(step: ModelStep, journal: list[freeroute.RouteStamp]) -> bool:
    """True only for a stamp FreeRoute itself appended during this ask."""
    return step.route is not None and any(step.route is seen for seen in journal)


def _reading_bound(
    held: plan_readings.HeldReading,
    *,
    session_id: str | None,
    space_id: str | None,
    manifest_hash: str,
    request_digest: str,
    scored_pack_id: str | None,
    now: float,
) -> bool:
    """True only for a hold made for this session, Space, manifest, request and pack, unexpired."""
    return (
        held.session_id == session_id
        and held.space_id == space_id
        and held.manifest_hash == manifest_hash
        and held.request_digest == request_digest
        and held.scored_pack_id == scored_pack_id
        and now < held.expires_at
    )


def _gate(sql: str, grant: Any) -> Any:
    from CortexOS.dms.sql_validate_gate import run_gate
    from CortexOS.dms.warehouse_db import load_semantic_layer

    return run_gate(sql, load_semantic_layer(), verified=grant)


def _tie_ordered_page(sql: str, grant: Any) -> str:
    """Generated SQL uses the same page tie as a governed metric."""
    from CortexOS.dms.sql_guardrail import apply_page_tiebreak
    from CortexOS.dms.warehouse_db import load_semantic_layer
    from CortexOS.execution.submit import execute_count

    semantic = load_semantic_layer()
    return apply_page_tiebreak(sql, semantic, execute_count(grant, sql))


def _sql_tables(sql: str) -> set[str]:
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(sql, read="duckdb")
    ctes = {fold_identifier(cte.alias_or_name) for cte in tree.find_all(exp.CTE)}
    return {fold_identifier(t.name) for t in tree.find_all(exp.Table) if t.name} - ctes


def _underived(sql: str, granted: set[str]) -> list[str]:
    """``OUTPUT_NOT_FROM_TABLE:<col>`` per output column not read from a granted table."""
    from CortexOS.dms.sql_lineage import lineage_violations

    return lineage_violations(sql, granted)


def _granted_names(grant: Any) -> set[str]:
    """The grant's table keys, folded only the way DuckDB folds names.

    Read from the verified manifest itself: a name list lower-cased with
    Unicode rules would turn a grant on ``"Äpfel"`` into ``äpfel``, which is a
    different DuckDB table.
    """
    return {fold_identifier(str(name)) for name in grant.row_predicates}


def _describe_catalog(
    request: plan_payload.PlanSqlRequest, granted: set[str]
) -> dict[str, dict[str, str | None]]:
    """The names a reading's statement may print: granted payload tables and their
    columns, each with the payload's type for it (``None`` when it gives none).

    A model-written alias is free text that executes fine, so ``plan_sql.describe``
    prints no name outside this (PICK-READING-01 review). The type lets it read a
    bare column as a condition only when the payload says it is BOOLEAN.
    ``request`` is the narrowed request the prompt saw.
    """
    names: dict[str, dict[str, str | None]] = {}
    for table in request.payload.tables:
        if fold_identifier(table.name.strip()) in granted:
            columns: dict[str, str | None] = {}
            for column in table.columns:
                columns.setdefault(column.name, column.type)
            names.setdefault(table.name.strip(), columns)
    return names


def _is_scored_round(scored_pack_id: str | None) -> bool:
    from CortexOS.memory.space_memory import get_space_memory

    return get_space_memory().is_scored_round(scored_pack_id)


# -- envelopes ------------------------------------------------------------------


def rung_retry(n: int) -> str:
    """Token for the Nth error-fed SQL rewrite on the first model. N starts at 1."""
    return f"error-fed-retry-{n}"


def _mark_rung(steps: list[StepStamp], rung: str) -> None:
    steps.append(StepStamp.cortex("rung", "cortex:plan-sql-rung", rung))


def model_calls_stamp(counts: dict[str, int]) -> dict[str, int] | None:
    """Logical ``complete()`` counts per step, plus ``total``.

    One count per call the ladder issues. Transport retries under that call
    are not included. None when nothing was called. ``total`` is the sum.
    """
    ordered: dict[str, int] = {}
    plan = counts.get(RUNG_PLAN, 0)
    if plan:
        ordered[RUNG_PLAN] = plan
    retries = sorted(
        (key for key in counts if key.startswith("error-fed-retry-") and counts[key]),
        key=lambda key: int(key.rsplit("-", 1)[-1]),
    )
    for key in retries:
        ordered[key] = counts[key]
    strong = counts.get(RUNG_STRONG, 0)
    if strong:
        ordered[RUNG_STRONG] = strong
    rec = counts.get(RUNG_RECONFIRM, 0)
    if rec:
        ordered[RUNG_RECONFIRM] = rec
    if not ordered:
        return None
    ordered["total"] = sum(ordered.values())
    return ordered


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
    """The confirm-no answer: an abstain with no rows and no SQL.

    The text is not an answer from data, so it never carries a session badge
    (CLAUDE.md section 8: a green badge on abstention prose is a P0).
    """
    note = "user answered no to the closest question"
    return {
        "answer": NOT_FOUND_ANSWER,
        "sql_used": None,
        "audit_id": audit_id,
        "route": ABSTAIN,
        "layer": ABSTAIN,
        "badge": ABSTAIN,
        "row_count": 0,
        "rows": [],
        "assumptions": [note, *(s.line() for s in steps)],
        "reconfirm": None,
        "plan_sql_rung": None,
        "provenance": {"layer": ABSTAIN, "badge": ABSTAIN, "assumptions": note},
        **_served(None, note),
    }


def _pipeline_failure_ticket(
    question: str,
    audit_id: str,
    *,
    reason: str,
    session_id: str | None,
    space_id: str | None,
    tables: Sequence[str],
) -> StepStamp:
    """Append a pipeline-failure ticket to the F1 audit ledger. Never raises.

    The ledger is immutable, so the ticket follows the ``goal_audit`` privacy
    policy: identifiers and verdicts, never content (PDPA). The question is
    carried as the SHA-256 of its UTF-8 bytes and its length, never as text;
    the ``audit_id`` joins the ticket to the ask for anyone allowed to see it.

    The returned step says whether the ticket was recorded or that it was not.
    It never shows the global ledger ``seq``. An audit outage does not turn the
    No into a 500, and a ticket that was not written is visible on the
    envelope, not swallowed.
    """
    payload = {
        "audit_id": audit_id,
        "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "question_chars": len(question),
        "reason": reason,
        "session_id": session_id,
        "space_id": space_id,
        "tables": list(tables),
    }
    try:
        from CortexOS.audit import resolve_ledger

        resolve_ledger().append(TICKET_ACTOR, PIPELINE_FAILURE_EVENT, payload)
    except Exception as exc:  # noqa: BLE001 - reported on the step and the log
        _LOG.warning(
            "plan_sql pipeline-failure ticket not recorded audit_id=%s: %s: %s",
            audit_id,
            type(exc).__name__,
            exc,
        )
        return StepStamp.cortex(
            "ticket", "cortex:pipeline-failure-ticket", f"not recorded: {type(exc).__name__}"
        )
    return StepStamp.cortex(
        "ticket", "cortex:pipeline-failure-ticket", f"{PIPELINE_FAILURE_EVENT} recorded"
    )


def reconfirm_answer(
    why: str, closest: str, readings: Sequence[tuple[str, int]] = ()
) -> str:
    """The answer text of a reconfirm envelope.

    With no answerable closest question it says so: Yes has nothing to run.
    With no readings the text is the 1.9 text byte for byte. With readings,
    each statement and its row count follow; never a value.
    """
    from CortexOS.dms.metric_choice import is_no_closest

    if is_no_closest(closest):
        text = (
            f"Reconfirm: {why} Closest question: none. {closest} "
            f"Yes runs that question, which has nothing to run and answers "
            f"'{NOT_FOUND_ANSWER}'. No answers '{NOT_FOUND_ANSWER}'."
        )
    else:
        text = (
            f"Reconfirm: {why} Closest question: {closest} "
            f"Yes runs that question. No answers '{NOT_FOUND_ANSWER}'."
        )
    if not readings:
        return text
    offered = " ".join(
        f"({i}) {statement}, {count} {'row' if count == 1 else 'rows'}."
        for i, (statement, count) in enumerate(readings, 1)
    )
    return f"{text} Or pick a reading: {offered} Picking one runs it again under your grant."


def ticket_metric_decline(
    result: dict[str, Any], body: AskRequest, *, accepted_none: bool
) -> dict[str, Any]:
    """Log the pipeline-failure ticket for a No on the engine's metric reconfirm.

    ``result`` is the engine's envelope for ``body``. Only its not-found answer
    (CLAUDE.md section 9: No answers not found and logs a ticket) gets one.
    ``accepted_none`` is a Yes on a reconfirm that offered no closest question.
    The ticket line is added to the assumptions; nothing else changes.
    """
    if result.get("answer") != NOT_FOUND_ANSWER or result.get("metric_choice") != "refused":
        return result
    stamp = _pipeline_failure_ticket(
        body.question,
        str(result.get("audit_id") or ""),
        reason=NO_CLOSEST_REASON if accepted_none else DECLINED_REASON,
        session_id=body.session_id,
        space_id=body.space_id,
        tables=(),
    )
    raw = result.get("assumptions")
    lines = [raw] if isinstance(raw, str) and raw.strip() else list(raw or [])
    return {**result, "assumptions": [*lines, stamp.line()]}


def _reconfirm_envelope(
    audit_id: str,
    steps: list[StepStamp],
    *,
    why: str,
    closest: str,
    stamp: StepStamp | None,
    rung: str,
    readings: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    answer = reconfirm_answer(
        why, closest, [(item["statement"], item["row_count"]) for item in readings]
    )
    call = stamp.served_call_id if stamp is not None else ""
    reconfirm: dict[str, Any] = {"why": why, "closest_question": closest}
    if readings:
        reconfirm["readings"] = [dict(item) for item in readings]
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
        "reconfirm": reconfirm,
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


def _picked(env: dict[str, Any], held: plan_readings.HeldReading) -> dict[str, Any]:
    """A served pick says which reading the user chose, first among its assumptions."""
    env["assumptions"] = [f"reading: {held.statement}", *env["assumptions"]]
    return env


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
    """Comparison literals that are not in the sampled values for that column.

    Names the model's own literal only. The sampled values are already in the
    model's prompt block and a failure detail can reach the user's reconfirm.
    """
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
            missed.append(f"{col.name}={str(lit.this)!r}")
    return missed


def _audit_raw(sql: str, exc: Any) -> None:
    """Unredacted engine text goes to the SQL audit log only, never an envelope or prompt."""
    from datetime import datetime, timezone

    from CortexOS.dms.sql_guardrail import AuditEntry, log_audit

    raw = getattr(exc, "raw_detail", None)
    log_audit(
        AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            original_sql=sql,
            safe_sql=None,
            violations=[f"EXPLAIN_FAILED:{raw}"] if raw else list(exc.violations),
            passed=False,
        )
    )


def _sample_values(
    grant: Any, request: plan_payload.PlanSqlRequest
) -> tuple[dict[str, dict[str, tuple[str, ...]]], str]:
    """Distinct values for payload columns the grant already allows. Never raises.

    Nothing is probed while ``CORTEX_GRANTED_CATALOG`` is off. A table the
    grant does not name is never probed, whatever the request carries. Only a
    column DMS marked ``masked: false`` (contract 1.9.0) is probed. A column
    marked masked, or one with no mask state (an older consumer, or a
    ``schema_context`` column the payload does not list), is never read here:
    its values do not reach a prompt, only its name and type do.

    At most ``SAMPLE_VALUES_PER_COLUMN`` distinct values a column, each passed
    through :func:`plan_payload.sample_text`, and the rendered block fits in
    :func:`plan_catalog.sample_budget`. The returned reason holds counts and
    sizes only, never a value.
    """
    if not granted_catalog_enabled():
        return {}, SAMPLE_OFF
    from CortexOS.execution.submit import execute_sql

    found: dict[str, dict[str, tuple[str, ...]]] = {}
    taken = 0
    errors = 0
    masked = 0
    ungranted = 0
    granted = _granted_names(grant)
    unmasked = plan_catalog.unmasked_columns(request.payload)
    budget = plan_catalog.sample_budget(request)
    for table in request.payload.tables:
        folded = fold_identifier(table.name.strip())
        if folded not in granted:
            ungranted += 1
            continue
        if not _IDENT.fullmatch(table.name):
            continue
        cols: dict[str, tuple[str, ...]] = {}
        for column in table.columns:
            if (folded, fold_identifier(column.name)) not in unmasked:
                masked += 1
                continue
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
                text = plan_payload.sample_text(value)
                if not text or text in vals:
                    continue
                vals.append(text)
                if len(vals) >= _SAMPLE_ROWS:
                    break
            if vals:
                cols[column.name] = tuple(vals)
        if cols:
            found[table.name] = cols
    fitted = plan_payload.fit_values(found, budget)
    nvals = sum(len(v) for cols in fitted.values() for v in cols.values())
    nbytes = len(plan_payload.render_values(fitted).encode("utf-8"))
    return fitted, (
        f"columns={taken} values={nvals} errors={errors} masked={masked} "
        f"ungranted={ungranted} bytes={nbytes} budget={budget}"
    )


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
        reading: str = plan_payload.FIRST_READING,
    ) -> ModelStep:
        del request, plan, prior_violations, reading
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


def grant_closest_question(body: AskRequest, *, verified: Any) -> str:
    """Cortex's own closest question for ``body``, built from the grant.

    The same shape the ladder falls back to when the model names no closest
    question: a sampled column of a payload table, else a row count of the first
    payload table (the first granted table when the ask carries no payload). It
    names only payload or granted tables and columns. Never raises; empty when
    there is nothing to name.
    """
    from CortexOS.dms.answer_engine import UngroundedSession, resolve_product_grant

    if body.dms_payload is None or not body.dms_payload.tables:
        tables = sorted(str(key) for key in getattr(verified, "row_predicates", {}) or {})
        return f"How many rows are in {tables[0]}?" if tables else ""
    request = plan_payload.PlanSqlRequest(body.question, body.dms_payload)
    try:
        grant, _, _ = resolve_product_grant(body.session_id, verified, space_id=body.space_id)
    except UngroundedSession:
        return _fallback_closest(request)
    sampled, _ = _sample_values(grant, request)
    return _fallback_closest(replace(request, column_values=sampled))


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


@contextmanager
def _stop_on_strict(steps: list[StepStamp]) -> Iterator[None]:
    """A strict pin refusal or 429 stops the ladder and keeps the served stamp.

    The exception already names retry_after_s. This records model_requested
    and model_served on the ask step list, then lets the error surface.
    """
    try:
        yield
    except freeroute.FreeRouteStrictError as exc:
        if exc.stamp is not None:
            steps.append(StepStamp.from_route("strict", exc.stamp, str(exc)))
        exc.ask_stamps = [step.public() for step in steps]
        raise


def plan_sql_answer(
    request: plan_payload.PlanSqlRequest,
    *,
    session_id: str | None,
    space_id: str | None,
    verified: Any,
    scored_pack_id: str | None = None,
    generator: PlanSqlGenerator | None = None,
    confirm: str | None = None,
    reading_id: str | None = None,
) -> dict[str, Any]:
    """Plan, then SQL, with grounded retries. Never widens past the signed grant.

    ``reading_id`` (contract 1.10.0) re-runs one held reading of an earlier
    split through every gate, with no model call; :class:`ReadingUnbound` when
    no reading with that id was held for this session, Space, manifest,
    request and scored pack, or it expired.
    """
    from CortexOS.dms.answer_engine import UngroundedSession, resolve_product_grant

    question = request.question
    offering = readings_on()
    # The request a hold is bound to, taken before any narrowing.
    digest = (
        plan_readings.request_digest(
            request.question, request.payload.model_dump(mode="json"), request.schema_context
        )
        if offering or reading_id is not None
        else ""
    )
    audit_id = str(uuid.uuid4())
    steps: list[StepStamp] = []
    failed_models: set[str] = set()
    failures: list[str] = []
    call_counts: dict[str, int] = {}
    vote_on = plan_sql_vote.vote_enabled()
    # Set when the vote split: no more writes, the ladder reconfirms.
    vote_split: list[str] = []
    # payload.prompt_sha256 of every first-reading SQL prompt this ask sent.
    # The vote's second reading is never one of these prompts.
    first_prompts: set[str] = set()
    # PICK-READING-01: each reading of a split vote with the rung of the model
    # that wrote it. Set only when the vote splits.
    split_readings: list[tuple[_Reading, str]] = []

    def _note_call(active: object, bucket: str) -> None:
        # A route refusal does not call complete(). Every other generator
        # method on this path calls it once.
        if isinstance(active, _RouteRefusal):
            return
        call_counts[bucket] = call_counts.get(bucket, 0) + 1

    def _with_calls(env: dict[str, Any]) -> dict[str, Any]:
        env["model_calls"] = model_calls_stamp(call_counts)
        return env

    def stop(code: str, detail: str, *, refused: bool = True) -> dict[str, Any]:
        return _with_calls(
            _not_answered(
                question, audit_id, steps, code=code, detail=detail, refused=refused
            )
        )

    try:
        grant, _, _ = resolve_product_grant(
            session_id, verified, space_id=space_id
        )
    except UngroundedSession as exc:
        steps.append(StepStamp.cortex("grant", "cortex:session-grant", f"refused: {exc}"))
        return stop(UNGROUNDED, str(exc))
    granted = _granted_names(grant)
    steps.append(
        StepStamp.cortex("grant", "cortex:session-grant", f"tables={sorted(granted)}")
    )

    widened = plan_payload.widening(request, granted)
    if widened:
        steps.append(StepStamp.cortex("payload", "cortex:payload-check", "refused"))
        return stop(widened[0].split(":", 1)[0], ", ".join(widened))
    if not granted_catalog_enabled():
        # Off: the string is not read, here or by the sampler's byte budget.
        request = replace(request, schema_context=None)
    request, unusable = plan_catalog.narrow(request, granted)
    if unusable:
        steps.append(
            StepStamp.cortex("payload", "cortex:payload-check", f"refused: {unusable}")
        )
        # A string that declares no table is DMS's over-budget fallback: an
        # abstain, as on POST /v1/insights. Every other code is a refusal.
        return stop(
            unusable,
            plan_catalog.refusal_detail(unusable),
            refused=unusable != plan_catalog.SCHEMA_CONTEXT_EMPTY,
        )
    selected = set(request.table_names())
    steps.append(
        StepStamp.cortex("payload", "cortex:payload-check", f"tables={sorted(selected)}")
    )

    if confirm == "no":
        steps.append(StepStamp.cortex("confirm", "cortex:plan-sql-confirm", "no"))
        steps.append(
            _pipeline_failure_ticket(
                question,
                audit_id,
                reason=DECLINED_REASON,
                session_id=session_id,
                space_id=space_id,
                tables=sorted(selected),
            )
        )
        return _with_calls(_not_found(audit_id, steps))

    held: plan_readings.HeldReading | None = None
    if reading_id is not None:
        from CortexOS.execution.drillthrough import manifest_content_hash

        held = plan_readings.store().get(reading_id)
        if held is None or not _reading_bound(
            held,
            session_id=session_id,
            space_id=verified.manifest.space_id,
            manifest_hash=manifest_content_hash(verified.manifest),
            request_digest=digest,
            scored_pack_id=scored_pack_id,
            now=time.time(),
        ):
            raise ReadingUnbound()
        steps.append(
            StepStamp.cortex(
                "pick", "cortex:plan-sql-pick", f"reading {held.index} of {held.of}"
            )
        )

    gen = generator or default_generator()
    # A sampled value goes only to OpenVault FreeRoute. Any other generator
    # gets the names and types and no probe runs for it.
    sampled: dict[str, dict[str, tuple[str, ...]]] = {}
    if held is not None:
        # A pick re-runs held SQL: no model is prompted, so nothing is sampled.
        sample_reason = "skipped: a pick re-runs held SQL"
    elif granted_catalog_enabled() and not _openvault_routed(gen):
        sample_reason = SAMPLE_NOT_OPENVAULT
    else:
        sampled, sample_reason = _sample_values(grant, request)
    steps.append(StepStamp.cortex("sample", "cortex:column-values", sample_reason))
    request = replace(request, column_values=sampled)
    if confirm == "yes":
        steps.append(StepStamp.cortex("confirm", "cortex:plan-sql-confirm", "yes"))

    def _hint(detail: str) -> str:
        # The database's own candidate lists are dropped; only a typo of an
        # unknown column from the granted catalog is offered.
        return plan_catalog.retry_detail(detail, plan_catalog.column_names(request, granted))

    def _round(
        active: PlanSqlGenerator,
        attempts: int,
        rung_of,
    ) -> dict[str, Any] | None:
        # One freeroute.complete per attempt, inside sql(). plan() is not called,
        # so the first attempt's single call is the ``plan`` rung's only count.
        folded = ModelStep(
            "plan",
            True,
            "",
            "folded into the sql call",
            StepStamp.cortex("plan", "cortex:plan-folded", "folded into the sql call"),
        )
        priors = list(failures)
        for attempt in range(attempts):
            sql_step = active.sql(request, folded, prior_violations=priors)
            if sql_step.prompt_sha256:
                first_prompts.add(sql_step.prompt_sha256)
            _note_call(active, rung_of(attempt))
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
            outcome = _use_sql(sql_step, folded, priors, rung_of(attempt))
            if outcome is not None:
                return outcome
            if vote_split:
                return None
        return None

    def _use_sql(
        sql_step: ModelStep,
        plan: ModelStep,
        priors: list[str],
        rung: str,
        collect: list[_Reading] | None = None,
        held: plan_readings.HeldReading | None = None,
    ) -> dict[str, Any] | None:
        """A final envelope, or None when ``priors`` gained a retryable error.

        With ``collect``, SQL that ran with rows is appended there instead of served.
        With ``held`` (a pick), SQL that ran with rows is served as that reading.
        """
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
            detail = _hint(f"{GATE_REFUSED}: {violations}")
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
        underived = _underived(served_sql, granted)
        if underived:
            # An answer column that no granted table supplies is a value the
            # model typed. Fed back, never executed or served.
            detail = (
                f"{NOT_FROM_TABLE}: {', '.join(underived)}; every output column must "
                "read a column of a granted table, or count or aggregate its rows"
            )
            steps.append(StepStamp.cortex("gate", "cortex:sql-gate", f"refused: {detail}"))
            freeroute.note_verdict(sql_step.route, "gate_fail")
            priors.append(detail)
            failures.append(detail)
            return None
        from CortexOS.dms.sql_literal_grounding import ungrounded_literals

        ungrounded = ungrounded_literals(request.question, served_sql)
        if ungrounded:
            # With sample values in hand the model can filter on an entity the
            # question never named and serve that entity's numbers. Fed back.
            detail = (
                f"{LITERAL_UNGROUNDED}: {', '.join(ungrounded)}; filter only on the "
                "identifiers the question names, and if the data does not hold them, say so"
            )
            steps.append(StepStamp.cortex("gate", "cortex:sql-gate", f"refused: {detail}"))
            freeroute.note_verdict(sql_step.route, "gate_fail")
            priors.append(detail)
            failures.append(detail)
            return None
        steps.append(StepStamp.cortex("gate", "cortex:sql-gate", "passed run_gate under the grant"))
        freeroute.note_verdict(sql_step.route, "gate_pass")
        return _execute(sql_step, plan, served_sql, priors, rung, collect, held)

    def _execute(
        sql_step: ModelStep,
        plan: ModelStep,
        served_sql: str,
        priors: list[str],
        rung: str,
        collect: list[_Reading] | None = None,
        held: plan_readings.HeldReading | None = None,
    ) -> dict[str, Any] | None:
        from CortexOS.dms.sql_validate_gate import (
            LakeValueDomainStale,
            SqlGateAbstain,
            redact_db_error,
        )
        from CortexOS.execution.manifest import ManifestError
        from CortexOS.execution.submit import execute_sql

        # Every refusal above has already returned. Tie the page here, then
        # run that one statement. The count is not a second served execute.
        served_sql = _tie_ordered_page(served_sql, grant)
        try:
            rows, _, _ = execute_sql(grant, served_sql)
        except LakeValueDomainStale as exc:
            # #402: the lake does not store the declared values of a column this
            # SQL names. Only a rebuild through the declaration fixes that, so
            # it is the answer, not a gate error to feed back: the one rewrite
            # that could run drops the column (for a sales question, every
            # movement summed as sales). The reason names columns, never values.
            steps.append(
                StepStamp.cortex(
                    "execute", "cortex:execute_sql", f"refused: {', '.join(exc.violations)}"
                )
            )
            _audit_raw(served_sql, exc)
            # No verdict on the model: its SQL passed the gate, the lake is what failed.
            return stop(exc.reason, "", refused=False)
        except ManifestError as exc:
            steps.append(StepStamp.cortex("execute", "cortex:execute_sql", f"refused: {exc.code}"))
            if exc.code in {"statement_not_allowed", "path_not_allowed"} or _ungranted(
                served_sql, granted
            ):
                code = MANIFEST_REFUSED
                return stop(code, f"{type(exc).__name__}:{exc.code}")
            detail = _hint(f"{EXECUTE_FAILED}: {type(exc).__name__}:{exc.code}")
            priors.append(detail)
            failures.append(detail)
            return None
        except SqlGateAbstain as exc:
            steps.append(StepStamp.cortex("execute", "cortex:execute_sql", "refused: EXPLAIN"))
            _audit_raw(served_sql, exc)
            # Redacted at the source: no caret context, no literal, no post-enforce SQL.
            detail = _hint(f"{GATE_REFUSED}: {exc}")
            priors.append(detail)
            failures.append(detail)
            return None
        except Exception as exc:  # noqa: BLE001 - an executor fault is retried, never a 500
            steps.append(StepStamp.cortex("execute", "cortex:execute_sql", type(exc).__name__))
            # Scanned for an unknown column only after DB-ERROR-TEXT redaction:
            # no LINE context, no quoted literal. The database text itself is
            # not fed back: only an unknown name the SQL wrote, with a typo
            # match from the granted catalog. Otherwise the class name alone.
            detail = f"{EXECUTE_FAILED}: {type(exc).__name__}"
            hint = plan_catalog.unknown_column_hint(
                redact_db_error(exc), served_sql, plan_catalog.column_names(request, granted)
            )
            if hint:
                detail = f"{detail}: {hint}"
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
        if collect is not None:
            collect.append((sql_step, served_sql, rows))
            return None
        if held is not None:
            # The user chose this reading between disagreeing ones: served, but
            # never as confirmed by a second reading.
            _note_vote(
                f"{plan_sql_vote.VOTE_SPLIT}: picked reading {held.index} of {held.of}; "
                "not confirmed by a second reading"
            )
            _mark_rung(steps, held.rung)
            return _with_calls(
                _picked(
                    _success(
                        question,
                        audit_id,
                        steps,
                        plan=RUNG_PICK_PLAN,
                        sql_step=sql_step,
                        rows=rows,
                        served_sql=served_sql,
                        rung=held.rung,
                    ),
                    held,
                )
            )
        if vote_on:
            picked = _vote((sql_step, served_sql, rows), rung)
            if picked is None or isinstance(picked, dict):
                return picked
            if picked[0] is not sql_step:
                plan = _NO_PLAN
            sql_step, served_sql, rows = picked
        _mark_rung(steps, rung)
        return _with_calls(
            _success(
                question,
                audit_id,
                steps,
                plan=plan,
                sql_step=sql_step,
                rows=rows,
                served_sql=served_sql,
                rung=rung,
            )
        )

    def _note_vote(reason: str) -> None:
        steps.append(StepStamp.cortex("vote", "cortex:plan-sql-vote", reason))

    def _same_prompt(active: Any, first: ModelStep, priors: list[str]) -> str:
        """Why the second reading's next prompt is not provably new, or ``""``.

        Computed from the exact messages ``active.sql`` would send, before it
        sends them. A generator that cannot show its prompt, or a first reading
        with no recorded prompt hash, is not provably distinct.
        """
        build = getattr(active, "sql_prompt", None)
        if not first.prompt_sha256 or build is None:
            return "its prompt cannot be shown to differ from the first reading's"
        would = plan_payload.prompt_sha256(
            build(
                request,
                _NO_PLAN,
                prior_violations=priors,
                reading=plan_payload.SECOND_READING,
            )
        )
        if would in first_prompts:
            return "its prompt would be the first reading's prompt"
        return ""

    def _check(
        active: Any,
        bucket: str,
        rung: str,
        attempts: int = 1,
        *,
        first: ModelStep | None = None,
        written: list[int] | None = None,
    ) -> _Reading | dict[str, Any] | None:
        """One more SQL in the second reading's framing, gated and run like the first.

        The reading, a stop envelope on a trust failure, or None when it gave no
        rows. A SQL that fails a gate, errors or returns no rows is fed back for
        up to ``attempts`` writes, as the ladder does for the first. Any other
        refusal of this SQL (an ungranted table included) only loses its vote:
        the candidate it checks already passed every gate.

        With ``first`` (the same-model second reading), a write is sent only when
        its prompt differs from every first-reading prompt this ask sent, and a
        write whose sent prompt hash is not shown to differ is not counted.
        Either way the reading is skipped (None) and a vote line says why.

        ``written`` receives the attempt index of the write that gave the
        reading (0 for the first write, N for error-fed rewrite N).
        """
        priors: list[str] = []
        for n in range(attempts):
            if first is not None:
                why = _same_prompt(active, first, priors)
                if why:
                    _note_vote(f"no second reading: {why}")
                    return None
            step = active.sql(
                request,
                _NO_PLAN,
                prior_violations=priors,
                reading=plan_payload.SECOND_READING,
            )
            _note_call(active, bucket if n == 0 else rung_retry(n))
            step = replace(step, stamp=replace(step.stamp, served_step=f"vote-{step.kind}"))
            steps.append(step.stamp)
            _remember(step, failed_models)
            security = _security_stop(step, journal)
            if security and security[0] != MODEL_UNAVAILABLE:
                return stop(security[0], security[1])
            if security or not step.ok or _not_served(step):
                return None
            if first is not None:
                sent = step.prompt_sha256
                if not sent or sent in first_prompts:
                    _note_vote(
                        "no second reading: the prompt it sent is not shown to differ "
                        "from the first reading's"
                    )
                    return None
                _note_vote(
                    f"second reading prompt sha256={sent[:12]} differs from "
                    f"the first reading's sha256={first.prompt_sha256[:12]}"
                )
            got: list[_Reading] = []
            _use_sql(step, _NO_PLAN, priors, rung, collect=got)
            if got:
                if written is not None:
                    written.append(n)
                return got[0]
        return None

    def _vote(first: _Reading, rung: str) -> _Reading | dict[str, Any] | None:
        """The reading to serve, a stop envelope, or None when no two readings agree.

        #386 folds the plan into the SQL call, so neither reading carries one.
        The second reading is written in its own framing and runs because its
        prompt differs from the first's. When it would not, or that cannot be
        shown, it is skipped and only the stronger route's reading can agree.
        """
        second_write: list[int] = []
        second = _check(gen, rung, rung, SQL_ATTEMPTS, first=first[0], written=second_write)
        if isinstance(second, dict):
            return second
        if second is not None and plan_sql_vote.agree(first[2], second[2]):
            how = "the same SQL" if plan_sql_vote.same_sql(first[1], second[1]) else "a second SQL"
            _note_vote(f"agreed: {how} written from a different prompt returned the same rows")
            return first
        third = _check(_stronger_generator(failed_models), RUNG_STRONG, rung)
        if isinstance(third, dict):
            return third
        if third is not None:
            for name, reading in (("first", first), ("second", second)):
                if reading is not None and plan_sql_vote.agree(reading[2], third[2]):
                    _note_vote(f"agreed: the stronger route's SQL returned the {name} SQL's rows")
                    return reading
        detail = f"{plan_sql_vote.VOTE_SPLIT}: {plan_sql_vote.SPLIT_DETAIL}"
        _note_vote(detail)
        failures.append(detail)
        vote_split.append(rung)
        # Each reading with the rung of the model that wrote it. The second is
        # always written by the first model, whichever round ran the vote.
        second_rung = (
            RUNG_PLAN if not second_write or second_write[0] == 0 else rung_retry(second_write[0])
        )
        split_readings[:] = [
            (reading, writer)
            for reading, writer in ((first, rung), (second, second_rung), (third, RUNG_STRONG))
            if reading is not None
        ]
        return None

    def _note_readings(reason: str) -> None:
        steps.append(StepStamp.cortex("readings", "cortex:plan-sql-readings", reason))

    def _offer() -> list[dict[str, Any]]:
        """The readings a split reconfirm offers, held for a pick. ``[]`` offers none.

        Any failure offers none and stamps why; the reconfirm is still returned.
        """
        if not offering or len(split_readings) < plan_readings.MIN_READINGS:
            return []
        chosen = split_readings[: plan_readings.MAX_READINGS]
        # Only a reading whose step passed the FreeRoute check in this ask is held.
        for (step, _sql, _rows), _writer in chosen:
            if _security_stop(step, journal) is not None or _not_served(step) or not step.ok:
                _note_readings("offered=0 reason=provenance")
                return []
        try:
            keep = _display_literal(question)
            names = _describe_catalog(request, granted)
            statements = [
                plan_describe.statement(sql, keep_literal=keep, granted=names)
                for (_s, sql, _r), _w in chosen
            ]
        except Exception as exc:  # noqa: BLE001 - nothing offered is safe; a 500 is not
            _note_readings(f"offered=0 reason=describe-failed ({type(exc).__name__})")
            return []
        if len(set(statements)) != len(statements):
            _note_readings("offered=0 reason=statement-collision")
            return []
        from CortexOS.execution.drillthrough import manifest_content_hash

        offers: list[dict[str, Any]] = []
        try:
            manifest_hash = manifest_content_hash(verified.manifest)
            expires = plan_readings.expiry(verified.manifest.expires_at)
            for index, (((step, sql, rows), writer), text) in enumerate(
                zip(chosen, statements, strict=True), 1
            ):
                held_reading = plan_readings.HeldReading(
                    reading_id=plan_readings.new_reading_id(),
                    model_text=step.text,
                    served_sql=sql,
                    statement=text,
                    row_count=len(rows),
                    rung=writer,
                    stamp=step.stamp,
                    index=index,
                    of=len(chosen),
                    session_id=str(session_id),
                    space_id=verified.manifest.space_id,
                    manifest_hash=manifest_hash,
                    request_digest=digest,
                    scored_pack_id=scored_pack_id,
                    expires_at=expires,
                )
                plan_readings.store().hold(held_reading)
                offers.append(
                    {
                        "reading_id": held_reading.reading_id,
                        "statement": text,
                        "row_count": len(rows),
                        "expires_at": held_reading.expires_iso(),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - nothing offered is safe; a 500 is not
            _note_readings(f"offered=0 reason=hold-failed ({type(exc).__name__})")
            return []
        _note_readings(f"offered={len(offers)}")
        return offers

    def _pick(held_reading: plan_readings.HeldReading) -> dict[str, Any]:
        """Re-run a held reading through every gate. Never a model call.

        Served only when every gate passes and it returns rows. A direct
        abstain only for destructive SQL, an ungranted table or a stale lake.
        Anything else reconfirms with no readings.

        No FreeRoute re-check here: no model is called in this ask, and the
        reading's provenance was checked in the offering ask, when written and
        again before it was held; the hold is bound to that ask.
        """
        held_step = ModelStep("sql", True, held_reading.model_text, "", held_reading.stamp, None)
        outcome = _use_sql(held_step, RUNG_PICK_PLAN, [], held_reading.rung, held=held_reading)
        if outcome is not None:
            return outcome
        why, closest = _parse_reconfirm("", failures, request)
        _mark_rung(steps, RUNG_RECONFIRM)
        return _with_calls(
            _reconfirm_envelope(
                audit_id,
                steps,
                why=why,
                closest=closest,
                stamp=None,
                rung=RUNG_RECONFIRM,
            )
        )

    split = freeroute.SPLIT_BENCHMARK if _is_scored_round(scored_pack_id) else None
    with freeroute.journal(split=split) as journal, _stop_on_strict(steps):
        if held is not None:
            return _pick(held)
        answered = _round(
            gen,
            SQL_ATTEMPTS,
            lambda n: RUNG_PLAN if n == 0 else rung_retry(n),
        )
        if answered is not None:
            return answered
        strong = _stronger_generator(failed_models)
        if not vote_split:
            # A split already asked the stronger route; another write is voted again.
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
            offered = _offer()
            _mark_rung(steps, RUNG_RECONFIRM)
            return _with_calls(
                _reconfirm_envelope(
                    audit_id,
                    steps,
                    why=why,
                    closest=closest,
                    stamp=served,
                    rung=RUNG_RECONFIRM,
                    readings=offered,
                )
            )
        rec = strong.reconfirm(request, tuple(failures)[:8])
        _note_call(strong, RUNG_RECONFIRM)
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
        offered = _offer()
        _mark_rung(steps, RUNG_RECONFIRM)
        return _with_calls(
            _reconfirm_envelope(
                audit_id,
                steps,
                why=why,
                closest=closest,
                stamp=rec.stamp if rec.ok else None,
                rung=RUNG_RECONFIRM,
                readings=offered,
            )
        )


__all__ = [
    "ABSTAIN",
    "CATALOG_ENV",
    "DECLINED_REASON",
    "EMPTY_RESULT",
    "ENABLED_ENV",
    "IMPLAUSIBLE_RESULT",
    "LAYER",
    "LITERAL_UNGROUNDED",
    "NO_CLOSEST_REASON",
    "NOT_FROM_TABLE",
    "NOT_FOUND_ANSWER",
    "PIPELINE_FAILURE_EVENT",
    "READING_UNAVAILABLE",
    "RECONFIRM",
    "RUNG_PICK_PLAN",
    "RUNG_PLAN",
    "RUNG_RECONFIRM",
    "RUNG_STRONG",
    "SQL_ATTEMPTS",
    "TICKET_ACTOR",
    "ReadingUnbound",
    "model_calls_stamp",
    "reconfirm_answer",
    "ticket_metric_decline",
    "rung_retry",
    "default_generator",
    "grant_closest_question",
    "granted_catalog_enabled",
    "plan_sql_answer",
    "plan_sql_enabled",
    "readings_on",
    "try_plan_sql",
]
