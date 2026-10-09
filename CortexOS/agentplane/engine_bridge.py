"""Bridge to the running Cortex engine's governed answer plane.

Crew asks the *served* engine over HTTP (the same ``POST /dms/query`` DMS
uses) rather than importing the answer path in-process, so the engine's
session binding, abstention rules and audit trail apply unchanged and the
answer-plane lane can keep evolving that code without this surface caring.

The envelope comes back with badge / sources / audit_id intact and the UI
renders them verbatim - including abstentions. An unreachable engine is a
visible envelope (``badge: engine_offline``), never a fabricated answer.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


def flatten_engine_ask(data: dict[str, Any], *, ok: bool = True) -> dict[str, Any]:
    """Crew HTTP and engine /v1/insights share this field list. Never invent numbers."""
    return {
        "ok": ok,
        "answer": data.get("answer", ""),
        "badge": data.get("badge"),
        "route": data.get("route"),
        "layer": data.get("layer"),
        "metric_id": data.get("metric_id"),
        "sources": data.get("sources"),
        "audit_id": data.get("audit_id"),
        "row_count": data.get("row_count"),
        "rows": data.get("rows"),
        "sql_used": data.get("sql_used"),
        "assumptions": data.get("assumptions"),
        "suggestions": data.get("suggestions"),
        "total_count": data.get("total_count"),
        "truncated": data.get("truncated"),
        "source_table": data.get("source_table"),
        "grant_kind": data.get("grant_kind"),
        "query_source": data.get("query_source"),
        "violations_blocked": data.get("violations_blocked"),
    }


class LocalEngineBridge:
    """In-process /dms/query equivalent for the engine's stable Insights API.

    Crew still uses HTTP ``EngineBridge`` so the served engine stays the SoT.
    ``/v1/insights`` lives on that engine, so looping HTTP to itself is wrong.
    """

    def __init__(self, session_id: str = "demo", space_id: str | None = None) -> None:
        self.session_id = session_id
        self.space_id = space_id
        self.base_url = "in-process"

    async def ask(self, question: str) -> dict[str, Any]:
        try:
            from CortexOS.dms.query_service import answer_question

            data = answer_question(
                question,
                session_id=self.session_id,
                space_id=self.space_id,
                require_grounding=True,
            )
        except Exception as exc:  # noqa: BLE001 — envelope must refuse, not 500-invent
            return {
                "ok": False,
                "answer": f"Cortex engine error ({type(exc).__name__}): {exc}",
                "badge": "engine_error",
            }
        if not isinstance(data, dict):
            return {
                "ok": False,
                "answer": "Cortex engine returned a non-object envelope",
                "badge": "engine_error",
            }
        return flatten_engine_ask(data)


class EngineBridge:
    def __init__(
        self,
        base_url: str,
        session_id: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.timeout = timeout
        self._transport = transport
        # OpenVault ov_ token if the engine is gated; never a second Cortex keystore.
        self.api_key = api_key if api_key is not None else (
            os.environ.get("CREW_ENGINE_KEY") or os.environ.get("DMS_API_KEY") or ""
        ).strip()

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout, transport=self._transport, headers=self._headers()
        )

    async def health(self) -> dict[str, Any]:
        try:
            async with self._client(5.0) as client:
                resp = await client.get(f"{self.base_url}/api/engine/activity")
            return {"ok": resp.status_code == 200, "url": self.base_url}
        except httpx.HTTPError as exc:
            return {"ok": False, "url": self.base_url, "detail": str(exc)}

    async def ask(self, question: str) -> dict[str, Any]:
        try:
            async with self._client(self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/dms/query",
                    json={"question": question, "session_id": self.session_id},
                )
        except httpx.HTTPError as exc:
            return {
                "ok": False,
                "answer": (
                    f"Cortex engine unreachable at {self.base_url} ({type(exc).__name__})."
                    " Start it with START_ENGINE.bat, or set CREW_ENGINE_URL."
                ),
                "badge": "engine_offline",
            }
        if resp.status_code != 200:
            detail = resp.text[:300]
            return {
                "ok": False,
                "answer": f"Cortex engine returned {resp.status_code}: {detail}",
                "badge": "engine_error",
            }
        data = resp.json()
        return flatten_engine_ask(data if isinstance(data, dict) else {})
