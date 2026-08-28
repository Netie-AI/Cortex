"""AirGPT sidecar bridge — the exact routes AirGPT's cortex_client.py calls.

AirGPT treats Cortex as an optional local security sidecar: pre-LLM gate,
intent classify, and hash-chained audit ledger. Response shapes must stay
compatible with that client:
  /dms/secure        → reads data["blocked"], then data["text"]
  /dms/audit/append  → reads data["ok"]
  /dms/audit/verify  → reads data["ok"]

O5 extends (does not replace) that contract with the Agent SDK bridge — the
first routes through which AirGPT agents can READ DMS objects and INVOKE
governed actions, with the engine SDK as the single gate:
  /dms/sidecar/query-objects → agent-visible columns only (PII never crosses)
  /dms/sidecar/call-action   → registry → RBAC → confirm → F8 runner → ledger
Denial verdicts map to status codes (403 rbac/filter_hidden, 404 unknown,
409 confirm_required) with {"verdict": ...} detail so the client can react.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(tags=["sidecar"])


class SecureRequest(BaseModel):
    text: str
    block_scam: bool = True


class ClassifyRequest(BaseModel):
    text: str


class AuditAppendRequest(BaseModel):
    actor: str = "airgpt"
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/dms/secure")
def dms_secure(
    req: SecureRequest,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    from packs.dms import secure_message

    _ = caller
    r = secure_message(req.text, block_scam=req.block_scam)
    return {"ok": True, "text": r["safe_text"], **r}


@router.post("/dms/classify")
def dms_classify(
    req: ClassifyRequest,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    from packs.dms import classify_message

    _ = caller
    return {"ok": True, **classify_message(req.text)}


@router.post("/dms/audit/append")
def dms_audit_append(
    req: AuditAppendRequest,
    caller: Caller = Depends(require_role("steward")),
) -> dict[str, Any]:
    from packs.dms import append_ledger

    # Actor comes from the authenticated key; client-supplied actor is
    # recorded as payload detail only (F7 design constraint).
    payload = dict(req.payload)
    if req.actor and req.actor != caller.actor:
        payload.setdefault("client_actor", req.actor)
    return {"ok": True, **append_ledger(caller.actor, req.event_type, payload)}


@router.get("/dms/audit/verify")
@router.post("/dms/audit/verify")
def dms_audit_verify(caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    from packs.dms import verify_ledger

    _ = caller
    return verify_ledger()


@router.get("/dms/audit/tail")
def dms_audit_tail(
    limit: int = 20,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    from dataclasses import asdict

    from packs.dms.audit.ledger import list_entries

    _ = caller
    limit = max(1, min(int(limit), 200))
    entries = list_entries(from_seq=0, limit=10_000)
    return {"ok": True, "events": [asdict(e) for e in entries[-limit:]]}


class QueryObjectsRequest(BaseModel):
    object_type: str
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = 50
    session_id: str | None = None


class CallActionRequest(BaseModel):
    action_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False
    run_id: str | None = None


_DENIAL_STATUS = {
    "not_found": 404,
    "unregistered": 404,
    "confirm_required": 409,
    "not_invocable": 400,
    "session_unbound": 409,
    "session_expired": 409,
    # rbac / filter_hidden / bad_actor / path_not_allowed default to 403
}


def _sdk_http_error(exc) -> HTTPException:
    status = _DENIAL_STATUS.get(exc.verdict, 403)
    return HTTPException(status_code=status, detail={"error": str(exc), "verdict": exc.verdict})


@router.post("/dms/sidecar/query-objects")
def sidecar_query_objects(
    req: QueryObjectsRequest,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    from CortexOS.agent_sdk import SdkDenied, query_objects

    try:
        rows = query_objects(
            req.object_type,
            req.filters,
            actor=caller,
            limit=req.limit,
            pack="dms",
            session_id=req.session_id,
        )
    except SdkDenied as exc:
        raise _sdk_http_error(exc) from exc
    except Exception as exc:  # warehouse missing/offline — bridge soft-fails closed
        raise HTTPException(
            status_code=503, detail={"error": f"warehouse unavailable: {exc}", "verdict": "backend"}
        ) from exc
    return {"ok": True, "object_type": req.object_type, "count": len(rows), "rows": rows}


@router.post("/dms/sidecar/call-action")
def sidecar_call_action(
    req: CallActionRequest,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    # Route stays viewer-min ON PURPOSE: the SDK's registry-driven role gate is
    # the single write-path authority (one gate, not two drifting ones), and its
    # denials are ledgered — an HTTP 403 here would be an unaudited refusal.
    from CortexOS.agent_sdk import SdkDenied
    from CortexOS.agent_sdk import call_action as sdk_call_action

    try:
        result = sdk_call_action(
            req.action_id,
            req.params,
            actor=caller,
            confirmed=req.confirmed,
            run_id=req.run_id,
            pack="dms",
        )
    except SdkDenied as exc:
        raise _sdk_http_error(exc) from exc
    except Exception as exc:
        # F8 runner denial (allowlist / compliance / path escape) — already ledgered
        verdict = getattr(exc, "verdict", "runner")
        detail = getattr(exc, "detail", {}) or {}
        status = 403 if verdict in ("allowlist", "path_escape") else 400
        raise HTTPException(
            status_code=status, detail={"error": str(exc), "verdict": verdict, **detail}
        ) from exc
    return {"ok": True, **result}


def register_sidecar_routes(app) -> None:
    app.include_router(router)
