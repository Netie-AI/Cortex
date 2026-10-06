"""VERIFIED-QUERY (#309) ask seam: steward-confirmed query reuse on contract ask.

Off unless ``CORTEX_VERIFIED_QUERY`` is set; when off, :func:`try_verified_query`
returns ``None`` before touching memory and the ask is unchanged.

The Space is the one the *signed* grant names (same as C-MEM). A matched query
is bound with ``$n`` placeholders and re-run through the same gate the contract
ask uses — ``run_gate`` then ``execute_sql`` under the session manifest, values
passed as bind parameters — so it is never wider than ``/v1/contract/submit``
for that session. Nothing cached is served.

Model-assisted matching is a second flag (``CORTEX_VERIFIED_QUERY_MODEL_MATCH``)
and goes through OpenVault FreeRoute only. The model may pick one of the
confirmed queries whose parameter shape already fits; it never sees rows or
SQL, never writes SQL and never chooses values.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from CortexOS.execution.manifest import VerifiedManifest
from CortexOS.memory.verified_query import (
    ModelMatcher,
    ModelPick,
    VerifiedQuery,
    VerifiedQueryLibrary,
    VerifiedReuse,
    get_verified_queries,
    placeholder_numbers,
)

ENABLED_ENV = "CORTEX_VERIFIED_QUERY"
MODEL_MATCH_ENV = "CORTEX_VERIFIED_QUERY_MODEL_MATCH"
ASK_ACTOR = "cortex:contract-ask"
MODEL_TASK = "verified_query_match"
LAYER = "verified_query"

_TRUTHY = {"1", "true", "on", "yes"}


def verified_query_enabled() -> bool:
    return (os.environ.get(ENABLED_ENV) or "").strip().lower() in _TRUTHY


def model_match_enabled() -> bool:
    return (os.environ.get(MODEL_MATCH_ENV) or "").strip().lower() in _TRUTHY


def ontology_terms(semantic: Mapping[str, Any]) -> dict[str, str]:
    """Glossary phrase -> canonical term (the column it maps to, else the term)."""
    out: dict[str, str] = {}
    for item in semantic.get("glossary") or []:
        term = str(item.get("term") or "").strip().lower()
        if term:
            out[term] = str(item.get("maps_to") or term).strip().lower().replace(" ", "_")
    return out


def freeroute_matcher(question: str, candidates: Sequence[VerifiedQuery]) -> ModelPick:
    """Ask FreeRoute which confirmed question this one repeats. Ids only, no SQL or rows."""
    from CortexOS.integrations import freeroute

    ids = {c.id for c in candidates}
    listing = "\n".join(f"{c.id}: {c.question}" for c in candidates)
    messages = [
        {
            "role": "system",
            "content": (
                "Pick the one verified question that asks the same thing as the user "
                "question, ignoring the specific SKU, location, warehouse and date values. "
                "Reply with its id only, or none."
            ),
        },
        {"role": "user", "content": f"Verified questions:\n{listing}\n\nUser question: {question}"},
    ]
    done = freeroute.complete(
        MODEL_TASK,
        messages,
        max_tokens=24,
        temperature=0.0,
        accept=lambda text: text.strip() in ids or text.strip().lower() == "none",
    )
    stamp = done.stamp
    served = (
        {
            "served_provider": stamp.served_provider,
            "served_model": stamp.served_model,
            "served_local": bool(stamp.served_local),
            "served_reason": stamp.served_reason,
        }
        if stamp is not None
        else {}
    )
    pick = done.text.strip() if done.ok else ""
    return ModelPick(pick if pick in ids else None, served)


def _memory_fields(reuse: VerifiedReuse) -> dict[str, Any]:
    return {
        "memory_ids_read": [reuse.entry.id],
        "memory_reads": [{**reuse.entry.provenance(), "served_at": reuse.stamps[0].served_at}],
        "reused": reuse.ok,
    }


def try_verified_query(
    question: str,
    *,
    session_id: str,
    space_id: str | None,
    verified: VerifiedManifest,
    scored_pack_id: str | None = None,
    library: VerifiedQueryLibrary | None = None,
    model: ModelMatcher | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]] | None:
    """``None`` when off or nothing matched; else ``(answer_or_None, memory_fields)``."""
    if not verified_query_enabled():
        return None
    from CortexOS.dms.answer_engine import UngroundedSession, resolve_product_grant
    from CortexOS.dms.query_service import route_question, synthesize_answer
    from CortexOS.dms.warehouse_db import load_semantic_layer

    try:
        grant, _, _ = resolve_product_grant(session_id, verified, space_id=space_id)
    except UngroundedSession:
        return None
    space = (grant.manifest.space_id or "").strip()
    if not space or route_question(question) == "blocked":
        return None
    semantic = load_semantic_layer()

    def _execute(sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        from CortexOS.dms.sql_validate_gate import SqlGateAbstain, run_gate
        from CortexOS.execution.submit import execute_sql

        gate = run_gate(sql, semantic)
        if not gate.passed or not gate.safe_sql:
            raise SqlGateAbstain("verified query failed the gate", violations=list(gate.violations))
        if placeholder_numbers(gate.safe_sql) != set(range(1, len(params) + 1)):
            raise SqlGateAbstain("gated SQL lost a bind placeholder", violations=["VQ_PARAM_SHAPE"])
        rows, _, _ = execute_sql(grant, gate.safe_sql, params=list(params))
        return rows

    if model is None and model_match_enabled():
        model = freeroute_matcher
    reuse = (library or get_verified_queries()).reuse(
        space_id=space,
        question=question,
        execute=_execute,
        actor=ASK_ACTOR,
        terms=ontology_terms(semantic),
        scored_pack_id=scored_pack_id,
        model=model,
    )
    if reuse is None:
        return None
    fields = _memory_fields(reuse)
    if not reuse.ok or reuse.rows is None:
        return None, fields
    rows = reuse.rows
    query, entry, bound = reuse.query, reuse.entry, reuse.match.bound
    values = ", ".join(f"{name}={value}" for name, value in bound.bound.items())
    fields["verified_query_id"] = query.id
    return (
        {
            "answer": synthesize_answer(rows, question),
            "sql_used": bound.display_sql,
            "audit_id": str(uuid.uuid4()),
            "route": LAYER,
            "row_count": len(rows),
            "rows": rows,
            "provenance": {
                "layer": LAYER,
                "badge": "session",
                "query_source": f"verified_query:{query.id}@memory:{entry.id}@v{entry.version}",
                "assumptions": (
                    f"Re-ran steward-confirmed verified query {query.id} from this Space on "
                    f"current data ({reuse.match.matched_by} match"
                    + (f"; {values}" if values else "")
                    + "). Reuse is not a validation."
                ),
            },
            **reuse.match.served,
            **fields,
        },
        fields,
    )
