"""Stable Cortex Insights API for DMS generative-ask and AirGPT skin.

Sibling to ``/v1/contract/*`` (not a cortex-contract version bump). Same
``run_insights`` as Crew chrome. Callers never send provider keys; OpenVault
FreeRoute holds them. AirGPT uses this path (plus ``/dms/sidecar/insights``);
there is no parallel invent stack.

Lives outside ``CortexOS/api`` so the engine API tree does not import Crew
(AST pin in tests/test_freeroute_core.py).

No from __future__ import annotations (FastAPI route module rule).
"""

import asyncio
import contextlib
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


def resolved_intent(body: InsightsAskIn) -> str:
    return (body.intent or body.question or "").strip()


def _peer_host(request: Request) -> str:
    return str(request.client.host if request.client else "")


def _caller_refused(purpose: str) -> JSONResponse:
    from CortexOS.integrations import freeroute as core

    body = {
        "ok": False,
        "status": "REFUSE",
        "purpose": purpose,
        "refused": insight_keys.CALLER_KEY_RULE,
        "values": [],
        "live_5000_ci": False,
    }
    core.stamp_router_fingerprint(body)
    return JSONResponse(body, status_code=401)


def _served_stamp(envelope: dict[str, Any]) -> Any:
    """The RouteStamp of the model call that served this envelope, or ``None``.

    Read from ``generative.stamp`` (the generate call's own stamp) through the
    same reader run_insights stamps with, so the HTTP envelope cannot disagree
    with it. ``None`` (no model call) keeps the #272 pending text.
    """
    from CortexOS.crew import insights as insights_mod

    return insights_mod._route_stamp_for_fingerprint(envelope)


def _annotate_transport(envelope: dict[str, Any]) -> None:
    """Say env-direct where the Crew route view still says ``connector: openvault``.

    ``RoutePick.connector`` is fixed in the #215 Crew layer, which this lane does
    not edit. The stamp's ``impl`` is what actually carried the call, so when it
    is the env-direct transport the route is re-labelled here, at the API edge.
    """
    from CortexOS.integrations import direct_providers

    gen = envelope.get("generative")
    if not isinstance(gen, dict):
        return
    stamp = gen.get("stamp")
    if not isinstance(stamp, dict) or stamp.get("impl") != direct_providers.IMPL:
        return
    route = gen.get("route")
    if isinstance(route, dict):
        route = dict(route)
        route["connector"] = direct_providers.IMPL
        route["custody"] = direct_providers.CUSTODY
        gen["route"] = route


def stamp_api(
    envelope: dict[str, Any],
    *,
    consumer: str = "dms",
    alias: str | None = None,
) -> dict[str, Any]:
    from CortexOS.integrations import freeroute as core

    out = dict(envelope)
    if isinstance(out.get("generative"), dict):
        out["generative"] = dict(out["generative"])
    _annotate_transport(out)
    core.stamp_router_fingerprint(out, _served_stamp(out))
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
    from CortexOS.integrations import direct_providers

    body = dict(freeroute_mod.public_identity())
    if direct_providers.enabled():
        # Crew's identity view hardcodes openvault; the env-direct opt-in is not that.
        body["custody"] = direct_providers.CUSTODY
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


CLIENT_GONE = (
    "client disconnected before this step; Cortex stopped spending "
    "(no further model or engine calls)"
)


class DisconnectGuard:
    """Stop spending once the HTTP caller has gone (DMS abandons at its timeout).

    Starlette does not cancel a handler when the client disconnects, so without
    this the climb keeps calling providers for an answer nobody will read. A
    listener task awaits the ASGI ``http.disconnect`` (the body is already
    read, so nothing else can arrive); the check runs on the event loop before
    each model call and engine ask. It cannot recall a call already in flight,
    only refuse the next one.

    ``Request.is_disconnected()`` is not used: it polls with a pre-cancelled
    scope, and behind a ``BaseHTTPMiddleware`` (the rate limiter) that poll is
    cancelled before it reaches the server, so it never reports a disconnect.
    """

    _MAX_STRAY_MESSAGES = 8

    def __init__(self, request: Request) -> None:
        self._request = request
        self._event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.skipped: list[str] = []

    @property
    def gone(self) -> bool:
        return self._event.is_set()

    async def _listen(self) -> None:
        try:
            for _ in range(self._MAX_STRAY_MESSAGES):
                message = await self._request.receive()
                if message.get("type") == "http.disconnect":
                    self._event.set()
                    return
        except Exception:  # noqa: BLE001 - an unreadable channel is not a disconnect
            return

    async def __aenter__(self) -> "DisconnectGuard":
        self._task = asyncio.ensure_future(self._listen())
        return self

    async def __aexit__(self, *exc: object) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def client_gone(self) -> bool:
        if not self._event.is_set():
            await asyncio.sleep(0)  # let the listener take a pending disconnect
        return self._event.is_set()

    def complete(self, runner: Any) -> Any:
        async def guarded(
            messages: list[dict[str, Any]] | None = None,
            *,
            purpose: str = "think",
            prompt: str = "",
            bearer: str | None = None,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if await self.client_gone():
                self.skipped.append(f"model:{purpose}")
                return {
                    "ok": False,
                    "status": "REFUSE",
                    "purpose": purpose,
                    "refused": CLIENT_GONE,
                    "values": [],
                    "live_5000_ci": False,
                }
            out = await runner(messages, purpose=purpose, prompt=prompt, bearer=bearer, **kwargs)
            return out if isinstance(out, dict) else {}

        return guarded

    def bridge(self, inner: Any) -> Any:
        guard = self

        class _GuardedBridge:
            base_url = getattr(inner, "base_url", "in-process")

            async def ask(self, question: str) -> dict[str, Any]:
                if await guard.client_gone():
                    guard.skipped.append("engine:ask")
                    return {"ok": False, "answer": CLIENT_GONE, "badge": "engine_offline"}
                out = await inner.ask(question)
                return out if isinstance(out, dict) else {}

        return _GuardedBridge()


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
    async with DisconnectGuard(request) as guard:
        result = await insights_mod.run_insights(
            intent,
            bridge=guard.bridge(
                LocalEngineBridge(session_id=body.session_id, space_id=body.space_id)
            ),
            ask=body.ask,
            generate=body.generate,
            bearer=bearer,
            complete=guard.complete(freeroute_mod.complete) if body.generate else None,
        )
    out = stamp_api(result, consumer=consumer, alias=alias)
    if guard.gone:
        out["client_disconnected"] = True
    return out


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
