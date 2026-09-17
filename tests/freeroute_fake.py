"""A scripted OpenVault for FreeRoute tests. No sockets.

Shapes are copied from the OpenVault source (D:\\OpenVault\\OpenMW\\openmw\\openvault),
not invented:

- ``GET /api/freeroute/status``: routers/freeroute.py freeroute_status (sealed,
  pooled_key_count, hops, spendable). Hop rows come from vault/fallback.py
  FallbackManager.status (key_id, label, provider, role, priority, precheck_status,
  circuit, failures, last_error, last_latency_ms, park_until, park_reason).
  Spendable rows are providers.py spendable_for_freeroute (id, chat_models, ...).
- ``GET /api/freeroute/ratelimit``: app.py freeroute_ratelimit. A verified
  ``ov_`` bearer answers with its key id as ``identity``; an unknown bearer from
  loopback is NOT a 401 there - OpenVault suppresses AuthRefusedError and
  answers 200 with identity ``local``.
- ``POST /v1/chat/completions``: vault/proxy.py chat_completions. Hops are walked
  in OpenVault order regardless of the requested model; a hop serves the
  requested id only when its provider catalogues it, otherwise its own first
  model (providers.py resolve_model). Refusals: 403 openvault_vault_sealed,
  503 openvault_no_keys, 400 openvault_non_retryable, 502
  openvault_fallback_exhausted, 401 from vault/auth.py resolve_caller.
"""

from __future__ import annotations

from collections import deque
from typing import Any

DEFAULT_BASE = "http://127.0.0.1:5000"


def hop(provider: str, priority: int, *, circuit: str = "closed", parked: bool = False) -> dict[str, Any]:
    return {
        "key_id": f"kid-{provider}-{priority}-do-not-serve-this-row-id",
        "label": f"{provider.upper()}_API_KEY",
        "provider": provider,
        "role": "free",
        "priority": priority,
        "precheck_status": "ok",
        "circuit": circuit,
        "failures": 0,
        "last_error": "upstream said gsk_leakedkeyfragment123 once" if provider == "groq" else None,
        "last_latency_ms": 120.0,
        "park_until": 1893456000.0 if parked else None,
        "park_reason": "quota" if parked else None,
    }


CATALOGUE: dict[str, list[str]] = {
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"],
    "cerebras": ["gpt-oss-120b", "llama-3.3-70b", "llama3.1-8b"],
    "deepseek": ["deepseek-v4-pro", "deepseek-v4-flash"],
    "openrouter": ["google/gemini-2.5-flash", "meta-llama/llama-3.3-70b-instruct"],
}


class FakeOpenVault:
    """Callable with the ``openvault_client.request_json`` signature."""

    hop = staticmethod(hop)

    def __init__(self, base: str = DEFAULT_BASE) -> None:
        self.base = base
        self.status_code = 200
        self.sealed: Any = False
        self.pooled: Any = 3
        self.hops: list[dict[str, Any]] = [hop("groq", 10), hop("cerebras", 40)]
        self.catalogue: dict[str, list[str]] = {k: list(v) for k, v in CATALOGUE.items()}
        self.identities: dict[str, str] = {}
        self.ratelimit_status = 200
        self.replies: deque[tuple[int, dict[str, Any] | None]] = deque()
        self.default_content = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
        self.calls: list[dict[str, Any]] = []
        self.non_openvault_calls: list[dict[str, Any]] = []

    # -- scripting --------------------------------------------------------
    def reply(
        self,
        content: str = "",
        *,
        status: int = 200,
        model: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        error_type: str = "",
        message: str = "",
    ) -> FakeOpenVault:
        if status != 200:
            self.replies.append((status, {"error": {"message": message or error_type, "type": error_type}}))
            return self
        body: dict[str, Any] = {"choices": [{"message": {"role": "assistant", "content": content}}]}
        if tool_calls:
            body["choices"][0]["message"]["tool_calls"] = tool_calls
        if model is not None:
            body["model"] = model
        self.replies.append((200, body))
        return self

    # -- views ------------------------------------------------------------
    def calls_to(self, path: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["path"].split("?", 1)[0] == path]

    @property
    def chat_calls(self) -> list[dict[str, Any]]:
        return self.calls_to("/v1/chat/completions")

    @property
    def status_calls(self) -> list[dict[str, Any]]:
        return self.calls_to("/api/freeroute/status")

    def served_for(self, requested: str) -> str:
        """OpenVault semantics: first unparked spendable hop decides; model is a preference."""
        for row in self.hops:
            if row.get("circuit") == "open" or row.get("park_until"):
                continue
            pool = self.catalogue.get(str(row.get("provider")), [])
            if not pool:
                continue
            return requested if requested in pool else pool[0]
        return ""

    # -- transport --------------------------------------------------------
    def __call__(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 5.0,
        base: str | None = None,
    ) -> tuple[int, dict[str, Any] | None]:
        record = {
            "method": method.upper(),
            "path": path,
            "body": dict(body or {}),
            "headers": dict(headers or {}),
            "base": base,
        }
        self.calls.append(record)
        if base is not None and base.rstrip("/") != self.base:
            self.non_openvault_calls.append(record)
            return 0, None
        route = path.split("?", 1)[0]
        auth = (headers or {}).get("Authorization", "")
        bearer = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if route == "/api/freeroute/status":
            if self.status_code != 200:
                return self.status_code, None
            return 200, {
                "ok": True,
                "surface": "freeroute",
                "sealed": self.sealed,
                "pooled_key_count": self.pooled,
                "hops": [dict(h) for h in self.hops],
                "spendable": [
                    {"id": pid, "chat_models": list(models), "openai_compatible": True}
                    for pid, models in self.catalogue.items()
                ],
                "spendable_count": len(self.catalogue),
            }
        if route == "/api/freeroute/ratelimit":
            if self.ratelimit_status != 200:
                return self.ratelimit_status, {"detail": "refused"}
            who = self.identities.get(bearer, "local")
            return 200, {"identity": who, "tier": "free", "remaining_tokens": 40000}
        if route == "/v1/chat/completions":
            if bearer and bearer not in self.identities:
                return 401, {"error": {"message": "that API key is not valid", "type": "auth"}}
            if self.sealed is True:
                return 403, {"error": {"message": "vault is sealed", "type": "openvault_vault_sealed"}}
            if self.replies:
                status, reply = self.replies.popleft()
            else:
                status, reply = 200, {
                    "choices": [{"message": {"role": "assistant", "content": self.default_content}}]
                }
            if status == 200 and isinstance(reply, dict) and "model" not in reply:
                reply = dict(reply)
                reply["model"] = self.served_for(str((body or {}).get("model") or ""))
            return status, reply
        return 404, {"detail": "not found"}
