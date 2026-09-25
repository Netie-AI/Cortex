"""Stable Cortex Insights API for DMS generative-ask and AirGPT skin.

Sibling to ``/v1/contract/*`` (not a cortex-contract version bump). Same
``run_insights`` as Crew chrome. Callers never send provider keys; OpenVault
FreeRoute holds them. AirGPT uses this path (plus ``/dms/sidecar/insights``);
there is no parallel invent stack.

Lives outside ``CortexOS/api`` so the engine API tree does not import Crew
(AST pin in tests/test_freeroute_core.py).

No from __future__ import annotations (FastAPI route module rule).
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from CortexOS.insights import keys as insight_keys

router = APIRouter(prefix="/v1/insights", tags=["insights"])

STATUSES = ("CERTIFIED", "ABSTAIN", "REFUSE")
STABLE_ASK = "POST /v1/insights"
AIRGPT_ALIAS = "POST /dms/sidecar/insights"
CREW_ALIAS = "POST /crew/insights"


class InsightsAskIn(BaseModel):
    intent: str = ""
    question: str = ""
    ask: bool = True
    generate: bool = False
    session_id: str = "demo"
    space_id: str | None = None
    consumer: str = Field(default="dms")
    # Caller-typed ontology plan (DMS compute_query retry). Not a plan_source stamp.
    query_plan: dict[str, Any] | None = None
    # Hint only. Never copied onto plan_source (relabel shortcut).
    mode: str = ""


def resolved_intent(body: InsightsAskIn) -> str:
    return (body.intent or body.question or "").strip()


def _peer_host(request: Request) -> str:
    return str(request.client.host if request.client else "")


def _caller_refused(purpose: str) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "status": "REFUSE",
            "purpose": purpose,
            "refused": insight_keys.CALLER_KEY_RULE,
            "values": [],
            "live_5000_ci": False,
        },
        status_code=401,
    )


def stamp_api(
    envelope: dict[str, Any],
    *,
    consumer: str = "dms",
    alias: str | None = None,
) -> dict[str, Any]:
    out = dict(envelope)
    out["api"] = {
        "stable": STABLE_ASK,
        "alias": alias,
        "consumer": consumer,
        "live_5000_ci": False,
        "cot_climb_complete": False,
        "prompt_harness_complete": False,
        "issue_212_complete": False,
        "issue_213_complete": False,
    }
    return out


def public_law_body(*, shell_public: dict[str, Any] | None = None) -> dict[str, Any]:
    from CortexOS.crew import insights as insights_mod

    body = insights_mod.public_law(shell_public=shell_public)
    body["stable"] = STABLE_ASK
    body["stable_ontology"] = "GET /v1/insights/ontology?q="
    body["stable_identity"] = "GET /v1/insights/identity"
    body["keys"] = "GET /v1/insights/keys"
    body["airgpt"] = AIRGPT_ALIAS + " (same run_insights; no parallel invent stack)"
    body["consumers"] = {
        "dms": STABLE_ASK + " - Cortex is compute; DMS stays consumer",
        "airgpt": STABLE_ASK + " or " + AIRGPT_ALIAS + " - skin, same path",
        "crew": CREW_ALIAS + " - chrome alias of the same run_insights",
    }
    body["live_5000_ci"] = False
    return body


def public_keys_body() -> dict[str, Any]:
    return insight_keys.key_posture()


def public_identity_body() -> dict[str, Any]:
    from CortexOS.crew import freeroute as freeroute_mod

    body = dict(freeroute_mod.public_identity())
    body["http"] = {
        "stable": "GET /v1/insights/identity",
        "crew": "GET /crew/identity",
    }
    return body


def ontology_body(intent: str) -> dict[str, Any]:
    from CortexOS.crew import insights as insights_mod

    ranking = insights_mod.retrieve_ontology(intent)
    return {
        "ok": bool(ranking.get("ok")),
        "phase": "ontology",
        "intent": intent,
        "ontology": ranking,
        "law": insights_mod.LAW,
        "live_5000_ci": False,
    }


async def execute_insights(
    body: InsightsAskIn,
    request: Request,
    *,
    consumer: str,
    alias: str | None = None,
) -> Any:
    from CortexOS.crew import freeroute as freeroute_mod
    from CortexOS.crew import insights as insights_mod
    from CortexOS.crew.engine_bridge import LocalEngineBridge

    intent = resolved_intent(body)
    if not intent:
        raise HTTPException(status_code=400, detail="intent or question is required")
    bearer: str | None = None
    if body.generate:
        armed = await freeroute_mod.run_core(freeroute_mod.arming)
        if armed.get("armed"):
            bearer = insight_keys.relay_bearer(
                request.headers.get("authorization") or "",
                _peer_host(request),
            )
            if bearer is None:
                return _caller_refused("generative_ask")
    result = await insights_mod.run_insights(
        intent,
        bridge=LocalEngineBridge(session_id=body.session_id, space_id=body.space_id),
        ask=body.ask,
        generate=body.generate,
        bearer=bearer,
        query_plan=body.query_plan,
    )
    return stamp_api(result, consumer=consumer, alias=alias)


@router.get("", operation_id="insights.law")
@router.get("/", include_in_schema=False)
async def insights_law() -> dict[str, Any]:
    """Ask+ontology map. No numbers. Consumers may GET-display."""
    return public_law_body()


@router.get("/ontology", operation_id="insights.ontology")
async def insights_ontology(q: str = "") -> dict[str, Any]:
    """Where + importance ranking only. Does not ask DMS and does not invent numbers."""
    intent = (q or "").strip()
    if not intent:
        raise HTTPException(status_code=400, detail="q is required")
    return ontology_body(intent)


@router.get("/identity", operation_id="insights.identity")
async def insights_identity() -> dict[str, Any]:
    """Stable Cortex API identity. Keys stay in OpenVault. Never returns a token."""
    return public_identity_body()


@router.get("/keys", operation_id="insights.keys")
async def insights_keys() -> dict[str, Any]:
    """Local vs cloud key posture. No secrets. live_5000_ci is always false."""
    return public_keys_body()


@router.post("", operation_id="insights.ask")
@router.post("/", include_in_schema=False)
async def insights_ask(body: InsightsAskIn, request: Request) -> Any:
    """NL then ontology then SQL then validate. CERTIFIED|ABSTAIN|REFUSE."""
    consumer = (body.consumer or "dms").strip().lower() or "dms"
    if consumer not in {"dms", "airgpt", "crew"}:
        consumer = "dms"
    return await execute_insights(body, request, consumer=consumer)


def register_insights_routes(app: Any) -> None:
    app.include_router(router)


__all__ = [
    "InsightsAskIn",
    "STATUSES",
    "execute_insights",
    "ontology_body",
    "public_identity_body",
    "public_keys_body",
    "public_law_body",
    "register_insights_routes",
    "resolved_intent",
    "router",
    "stamp_api",
]
