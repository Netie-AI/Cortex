"""Stable DMS↔engine contract HTTP surface.

These five operationIds are the allowlisted contract route set. OpenAPI export
asserts the published spec contains exactly this set — nothing more, nothing
less — so the wire does not drift with install profile or optional planes.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from packages.cortex_contract.answer import Answer, AskRequest, Badge, Provenance
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


@router.post("/ask", response_model=Answer, operation_id="ask")
async def contract_ask(body: AskRequest) -> Answer:
    """Answer a governed question (DMS ask plane)."""
    from CortexOS.dms.answer_engine import answer as answer_engine

    result = answer_engine(body.question, session_id=body.session_id)
    if isinstance(result, Answer):
        return result
    data = dict(result)
    # Engine dict may omit contract-required fields on older paths — fill safely.
    if "provenance" not in data:
        data["provenance"] = Provenance(layer="engine", badge=Badge.SESSION)
    return Answer.model_validate(data)


@router.post("/submit", response_model=QueryResult, operation_id="submit")
async def contract_submit(body: SubmitRequest) -> QueryResult:
    """Submit a plan under a signed session manifest."""
    _ = body
    # Wired by the execution plane (C3 manifest verification + run_plan). Until
    # that lands, refuse rather than silently accepting unsigned work.
    raise HTTPException(
        status_code=501,
        detail={
            "error": "FeatureNotInstalled",
            "feature": "contract.submit",
            "message": "Signed submit path not wired in this build",
        },
    )


@router.post("/ledger/append", response_model=LedgerEntry, operation_id="ledger.append")
async def contract_ledger_append(body: LedgerAppendRequest) -> LedgerEntry:
    from packs.dms.audit import ledger as audit_ledger

    entry = audit_ledger.append(body.actor, body.event_type, body.payload)
    return LedgerEntry.model_validate(_as_mapping(entry))


@router.post("/ledger/verify", response_model=ChainVerification, operation_id="ledger.verify")
async def contract_ledger_verify(body: LedgerVerifyRequest) -> ChainVerification:
    from packs.dms.audit import ledger as audit_ledger

    result = audit_ledger.verify(start_seq=body.start_seq)
    return ChainVerification.model_validate(_as_mapping(result))


@router.get("/tools", response_model=ToolRegistryResponse, operation_id="tool.registry")
async def contract_tool_registry() -> ToolRegistryResponse:
    """List tools DMS may invoke through the contract runtime."""
    from CortexOS.execution.tool_runner import allowed_action_tools

    tools = [
        ToolSpec(id=tool_id, class_name=ToolClass.APPLY, description=tool_id)
        for tool_id in sorted(allowed_action_tools())
    ]
    return ToolRegistryResponse(tools=tools)


def register_contract_routes(app: Any) -> None:
    app.include_router(router)


__all__ = ["CONTRACT_ROUTE_IDS", "register_contract_routes", "router"]
