"""Stable DMS↔engine contract HTTP surface.

Allowlisted contract operationIds — keep in lockstep with scripts/export_openapi.py.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from packages.cortex_contract.answer import (
    Answer,
    AskRequest,
    Badge,
    DrillthroughRequest,
    DrillthroughResponse,
    Provenance,
)
from packages.cortex_contract.execution import QueryResult, SubmitRequest
from packages.cortex_contract.ledger import ChainVerification, LedgerEntry
from packages.cortex_contract.tools import ToolClass, ToolSpec

# Canonical contract route IDs — keep in lockstep with scripts/export_openapi.py.
CONTRACT_ROUTE_IDS: frozenset[str] = frozenset(
    {
        "ask",
        "submit",
        "ledger.append",
        "ledger.verify",
        "tool.registry",
        "drillthrough",
    }
)

router = APIRouter(prefix="/v1/contract", tags=["contract"])


class LedgerAppendRequest(BaseModel):
    actor: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class LedgerVerifyRequest(BaseModel):
    start_seq: int = 0


class ToolRegistryResponse(BaseModel):
    tools: list[ToolSpec]


def _as_mapping(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    raise TypeError(f"cannot map {type(value)!r} to dict")


def _http_for_submit_status(status: str) -> int:
    if status in {"pool_saturated"}:
        return 429
    if status in {
        "session_unbound",
        "session_expired",
        "drillthrough_session_mismatch",
    }:
        return 409
    if status in {
        "pool_mismatch",
        "pool_required",
        "manifest_signature_invalid",
        "manifest_unknown_issuer",
        "path_not_allowed",
        "statement_not_allowed",
        "sql_not_analyzable",
        "manifest_expired",
        "manifest_not_yet_valid",
    }:
        return 403
    if status in {
        "manifest_malformed",
        "sql_required",
        "drillthrough_token_invalid",
        "drillthrough_error",
    }:
        return 400
    return 403


_FLAT_BADGE: dict[str, Badge] = {
    "certified": Badge.CERTIFIED,
    "governed_metric": Badge.GOVERNED_METRIC,
    "query_skill": Badge.QUERY_SKILL,
    "session": Badge.SESSION,
    "generated": Badge.QUERY_SKILL,  # L2_VALIDATED on DMS side
    "l2_validated": Badge.QUERY_SKILL,
    "abstain": Badge.ABSTAIN,
    "blocked": Badge.BLOCKED,
}


def _assumptions_str(data: dict[str, Any]) -> str | None:
    raw = data.get("assumptions")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, list) and raw:
        first = raw[0]
        return str(first).strip() if first else None
    return None


def _is_abstain_signal(data: dict[str, Any]) -> bool:
    route = str(data.get("route") or "").lower()
    badge = str(data.get("badge") or "").lower()
    layer = str(data.get("layer") or "").lower()
    return route in {"needs_clarification", "abstain", "blocked"} or badge in {
        "abstain",
        "blocked",
    } or layer in {"abstain", "blocked"}


def _provenance_from_flat(data: dict[str, Any]) -> Provenance:
    """Build provenance from answer_engine flat fields — never invent SESSION on abstain."""
    route = str(data.get("route") or "").lower()
    badge_raw = str(data.get("badge") or "").lower()
    layer = str(data.get("layer") or "engine")
    assumptions = _assumptions_str(data)
    if route == "blocked" or badge_raw == "blocked" or layer == "blocked":
        return Provenance(
            layer="blocked",
            badge=Badge.BLOCKED,
            assumptions=assumptions,
            metric_id=data.get("metric_id") if isinstance(data.get("metric_id"), str) else None,
        )
    if _is_abstain_signal(data):
        return Provenance(
            layer="abstain" if layer in {"", "engine"} else layer,
            badge=Badge.ABSTAIN,
            assumptions=assumptions,
            metric_id=data.get("metric_id") if isinstance(data.get("metric_id"), str) else None,
        )
    badge = _FLAT_BADGE.get(badge_raw, Badge.SESSION)
    return Provenance(
        layer=layer or "engine",
        badge=badge,
        assumptions=assumptions,
        metric_id=data.get("metric_id") if isinstance(data.get("metric_id"), str) else None,
    )


def _enrich_answer(data: dict[str, Any], *, session_id: str, verified: Any) -> dict[str, Any]:
    """Attach T7 answer_id + drillthrough_token when SQL is present.

    Abstain/blocked flat answers must carry Provenance.badge=ABSTAIN|BLOCKED —
    never default to SESSION (that stamped L2_VALIDATED on DMS envelopes).
    """
    import uuid

    from CortexOS.execution.drillthrough import manifest_content_hash, mint_token

    if "provenance" not in data or data.get("provenance") is None:
        data["provenance"] = _provenance_from_flat(data)
    elif _is_abstain_signal(data):
        # Override a wrong SESSION default if callers pre-set provenance.
        data["provenance"] = _provenance_from_flat(data)

    answer_id = data.get("answer_id") or data.get("audit_id") or str(uuid.uuid4())
    data["answer_id"] = answer_id
    assumptions = data.get("assumptions")
    if isinstance(assumptions, str) and assumptions.strip():
        data["assumptions"] = [assumptions.strip()]
    elif not isinstance(assumptions, list):
        data["assumptions"] = []
    data.setdefault("contributing_sources", [])
    sql = data.get("sql_used")
    # Never mint drillthrough for abstain/blocked answers.
    if _is_abstain_signal(data):
        data["drillthrough_token"] = None
        data["sql_used"] = None
    elif isinstance(sql, str) and sql.strip() and not data.get("drillthrough_token"):
        try:
            data["drillthrough_token"] = mint_token(
                answer_id=str(answer_id),
                session_id=session_id,
                manifest_hash=manifest_content_hash(verified.manifest),
                sql=sql,
            )
        except Exception:  # noqa: BLE001 — token is additive; never break ask
            data["drillthrough_token"] = None
    return data


@router.post("/ask", response_model=Answer, operation_id="ask")
async def contract_ask(body: AskRequest) -> Answer:
    """Answer a governed question (DMS ask plane). Requires a prior submit bind."""
    from CortexOS.dms.answer_engine import answer as answer_engine
    from CortexOS.execution.manifest import ManifestError
    from CortexOS.execution.session_manifests import (
        SessionExpired,
        SessionUnbound,
        get_session_registry,
    )

    try:
        verified = get_session_registry().resolve(body.session_id)
    except SessionUnbound as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except SessionExpired as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    try:
        result = answer_engine(
            body.question,
            session_id=body.session_id,
            space_id=body.space_id,
            verified=verified,
        )
    except ManifestError as exc:
        code = getattr(exc, "code", "manifest_error")
        raise HTTPException(
            status_code=_http_for_submit_status(code),
            detail={"code": code, "message": str(exc)},
        ) from exc

    if isinstance(result, Answer):
        data = result.model_dump()
    else:
        data = dict(result)
    data = _enrich_answer(data, session_id=body.session_id, verified=verified)

    from CortexOS.execution.telemetry import new_run_id, record_run

    row_count = data.get("row_count")
    record_run(
        run_id=str(data.get("audit_id") or new_run_id()),
        kind="ask",
        status=str(data.get("badge") or data.get("layer") or data.get("route") or "ok"),
        session_id=body.session_id,
        pool_id=verified.manifest.pool_id,
        row_count=int(row_count) if isinstance(row_count, int) else None,
        issuer_kid=verified.issuer_kid,
        error=_assumptions_str(data) if _is_abstain_signal(data) else None,
    )

    return Answer.model_validate(data)


@router.post("/drillthrough", response_model=DrillthroughResponse, operation_id="drillthrough")
async def contract_drillthrough(body: DrillthroughRequest) -> DrillthroughResponse:
    """Re-derive contributing rows under the same session manifest (T7)."""
    from CortexOS.execution.drillthrough import (
        DrillthroughError,
        execute_drillthrough,
    )

    try:
        result = execute_drillthrough(body.token)
    except DrillthroughError as exc:
        raise HTTPException(
            status_code=_http_for_submit_status(getattr(exc, "code", "drillthrough_error")),
            detail={"code": getattr(exc, "code", "drillthrough_error"), "message": str(exc)},
        ) from exc
    return DrillthroughResponse.model_validate(result)


@router.post("/submit", response_model=QueryResult, operation_id="submit")
async def contract_submit(body: SubmitRequest) -> QueryResult:
    """Submit a plan under a signed session manifest (C4)."""
    from CortexOS.execution.submit import submit_request

    result = submit_request(body)
    if result.ok:
        return result
    raise HTTPException(
        status_code=_http_for_submit_status(result.status),
        detail={
            "code": result.status,
            "message": result.error or result.status,
            "run_id": result.run_id,
        },
    )


def _ledger() -> Any:
    """The ledger the active pack registered, or 501 if this install has none.

    Engine-owned seam (``CortexOS.audit``) rather than a direct ``packs.dms``
    import: CortexOS must not reach across the C2 boundary.
    """
    from CortexOS.audit import LedgerNotRegistered, resolve_ledger

    try:
        return resolve_ledger()
    except LedgerNotRegistered as exc:
        raise HTTPException(
            status_code=501,
            detail={
                "error": "FeatureNotInstalled",
                "feature": "contract.ledger",
                "message": str(exc),
            },
        ) from exc


@router.post("/ledger/append", response_model=LedgerEntry, operation_id="ledger.append")
async def contract_ledger_append(body: LedgerAppendRequest) -> LedgerEntry:
    # No docstring: FastAPI publishes it as the operation `description`, and the
    # released spec (contract/openapi-1.1.0.json) has none for this operation.
    entry = _ledger().append(body.actor, body.event_type, body.payload)
    return LedgerEntry.model_validate(_as_mapping(entry))


@router.post("/ledger/verify", response_model=ChainVerification, operation_id="ledger.verify")
async def contract_ledger_verify(body: LedgerVerifyRequest) -> ChainVerification:
    # No docstring — see contract_ledger_append.
    ledger = _ledger()
    if hasattr(ledger, "verify_chain"):
        result = ledger.verify_chain(start_seq=body.start_seq)
    else:
        result = ledger.verify(start_seq=body.start_seq)
    return ChainVerification.model_validate(_as_mapping(result))


@router.get("/tools", response_model=ToolRegistryResponse, operation_id="tool.registry")
async def contract_tool_registry() -> ToolRegistryResponse:
    """List tools DMS may invoke through the contract runtime."""
    from CortexOS.ontology.registry import load_tool_specs, pack_dir_for

    tools = [
        ToolSpec(
            id=action.id,
            class_name=ToolClass(action.tool_class or "apply"),
            description=action.description or action.id,
        )
        for action in load_tool_specs(pack_dir_for())
    ]
    return ToolRegistryResponse(tools=tools)


@router.post("/jwks/refresh")
def contract_jwks_refresh() -> dict[str, Any]:
    """Cold-path JWKS refresh after DMS mints a new OpenVault intermediate."""
    from CortexOS.execution.submit import refresh_jwks

    return refresh_jwks()


def register_contract_routes(app: Any) -> None:
    app.include_router(router)


__all__ = ["CONTRACT_ROUTE_IDS", "register_contract_routes", "router"]
