"""OpenVault FreeRoute adapter. Loopback sends no bearer.

A dummy Authorization (openvault-loopback, EMPTY) 401s. Crew already learned
this: distill docs/subagents_findings/2026-08-23_crew-openvault-desktop.md
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter

_DUMMY_KEYS = frozenset(
    {
        "",
        "empty",
        "none",
        "null",
        "changeme",
        "openvault-loopback",
    }
)


def resolved_openvault_token() -> str:
    """Real vault token, or empty for loopback. Never returns a dummy that 401s."""
    raw = (os.environ.get("OPENVAULT_TOKEN") or "").strip()
    if not raw or raw.lower() in _DUMMY_KEYS:
        return ""
    return raw


class OpenVaultAdapter(LLMAdapter):
    """POST {api_base}/chat/completions. api_base is the OpenAI-style .../v1 root."""

    def __init__(
        self,
        *,
        api_base: str,
        api_key: str | None = None,
        usd_per_million_prompt: float = 0.2,
        usd_per_million_completion: float = 0.2,
        myr_per_usd: float = 4.7,
        timeout: float = 180.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        token = (api_key if api_key is not None else resolved_openvault_token()).strip()
        self.api_key = "" if token.lower() in _DUMMY_KEYS else token
        self._usd_in = usd_per_million_prompt / 1_000_000
        self._usd_out = usd_per_million_completion / 1_000_000
        self._myr_usd = myr_per_usd
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _model(raw: str) -> str:
        name = (raw or "").strip() or "auto"
        if name.startswith("openai/"):
            name = name.split("/", 1)[1] or "auto"
        return name or "auto"

    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        t0 = time.perf_counter()
        messages: list[dict[str, Any]] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.prompt})
        payload: dict[str, Any] = {
            "model": self._model(req.model),
            "messages": messages,
            "max_tokens": req.max_tokens,
            "stream": bool(req.stream),
        }
        if req.tools:
            payload["tools"] = list(req.tools)
            payload["tool_choice"] = "auto"
        url = f"{self.api_base}/chat/completions"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
        if resp.status_code != 200:
            raise RuntimeError(f"OpenVault HTTP {resp.status_code}: {resp.text[:200]}")
        raw = resp.json() if resp.content else {}
        if not isinstance(raw, dict):
            raw = {}
        choice = (raw.get("choices") or [{}])[0]
        if not isinstance(choice, dict):
            choice = {}
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            message = {}
        usage = raw.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        return AdapterResponse(
            content=str(message.get("content") or ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            raw=raw,
        )

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        usd = (prompt_tokens * self._usd_in) + (completion_tokens * self._usd_out)
        return round(usd * self._myr_usd, 6)
