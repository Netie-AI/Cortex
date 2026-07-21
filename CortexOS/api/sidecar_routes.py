"""AirGPT sidecar bridge — the exact routes AirGPT's cortex_client.py calls.

AirGPT treats Cortex as an optional local security sidecar: pre-LLM gate,
intent classify, and hash-chained audit ledger. Response shapes must stay
compatible with that client:
  /dms/secure        → reads data["blocked"], then data["text"]
  /dms/audit/append  → reads data["ok"]
  /dms/audit/verify  → reads data["ok"]
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends
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
    payload: Dict[str, Any] = Field(default_factory=dict)


@router.post("/dms/secure")
def dms_secure(
    req: SecureRequest,
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    from packs.dms import secure_message

    _ = caller
    r = secure_message(req.text, block_scam=req.block_scam)
    return {"ok": True, "text": r["safe_text"], **r}


@router.post("/dms/classify")
def dms_classify(
    req: ClassifyRequest,
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    from packs.dms import classify_message

    _ = caller
    return {"ok": True, **classify_message(req.text)}


@router.post("/dms/audit/append")
def dms_audit_append(
    req: AuditAppendRequest,
    caller: Caller = Depends(require_role("steward")),
) -> Dict[str, Any]:
    from packs.dms import append_ledger

    # Actor comes from the authenticated key; client-supplied actor is
    # recorded as payload detail only (F7 design constraint).
    payload = dict(req.payload)
    if req.actor and req.actor != caller.actor:
        payload.setdefault("client_actor", req.actor)
    return {"ok": True, **append_ledger(caller.actor, req.event_type, payload)}


@router.get("/dms/audit/verify")
@router.post("/dms/audit/verify")
def dms_audit_verify(caller: Caller = Depends(require_role("viewer"))) -> Dict[str, Any]:
    from packs.dms import verify_ledger

    _ = caller
    return verify_ledger()


@router.get("/dms/audit/tail")
def dms_audit_tail(
    limit: int = 20,
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    from dataclasses import asdict

    from packs.dms.audit.ledger import list_entries

    _ = caller
    limit = max(1, min(int(limit), 200))
    entries = list_entries(from_seq=0, limit=10_000)
    return {"ok": True, "events": [asdict(e) for e in entries[-limit:]]}


def register_sidecar_routes(app) -> None:
    app.include_router(router)
