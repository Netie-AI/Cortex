"""Crew talks to OpenVault FreeRoute. Secrets stay in the vault.

Loopback POST /v1/chat/completions with no bearer (OpenVault treats 127.0.0.1
as the unmetered local tier). Crew never copies Groq/OpenRouter/Cursor secrets
into its own process. A silent walk onto a dead Cortex primary is OpenVault's
problem; we send model=auto and surface the typed error.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from CortexOS.crew.llm import LLMError, LLMResult, ToolCall, _parse_args

DEFAULT_URL = "http://127.0.0.1:5000"


DEFAULT_CURSOR_MODEL = "grok-4.6"


def base_url() -> str:
    return os.environ.get("CREW_OPENVAULT_URL", DEFAULT_URL).rstrip("/")


def cursor_model() -> str:
    return os.environ.get("CREW_CURSOR_MODEL", DEFAULT_CURSOR_MODEL).strip() or DEFAULT_CURSOR_MODEL


def cursor_key_status() -> dict[str, Any]:
    """Public Cursor key presence. Never returns the secret."""
    secret = os.environ.get("CURSOR_API_KEY", "").strip()
    return {
        "configured": bool(secret),
        "chars": len(secret),
        "model": cursor_model(),
        "source": "env" if secret else "",
    }


def resolve_ov_model(model: str) -> str:
    """Map crew model strings onto FreeRoute. Prefer grok-4.6 (high), never fast."""
    raw = (model or "").strip()
    if raw.startswith("openvault/"):
        raw = raw.split("/", 1)[1].strip()
    if raw in {"", "auto"}:
        override = os.environ.get("CREW_OPENVAULT_MODEL", "").strip()
        if override:
            return override
        if os.environ.get("CURSOR_API_KEY", "").strip() or os.environ.get("XAI_API_KEY", "").strip():
            return cursor_model()
        return "auto"
    if "fast" in raw.lower() and "grok" in raw.lower():
        return cursor_model()
    return raw


def healthz(timeout: float = 1.5) -> dict[str, Any]:
    if os.environ.get("CREW_OPENVAULT", "1") == "0":
        return {"ok": False, "detail": "CREW_OPENVAULT=0"}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base_url()}/api/healthz")
        if resp.status_code != 200:
            return {"ok": False, "detail": f"HTTP {resp.status_code}"}
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        return {"ok": True, "url": base_url(), "mesh": data.get("mesh")}
    except httpx.HTTPError as exc:
        return {"ok": False, "url": base_url(), "detail": f"{type(exc).__name__}"}


def _tools_from_choice(message: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for i, tc in enumerate(message.get("tool_calls") or []):
        fn = tc.get("function") or {}
        calls.append(
            ToolCall(
                id=str(tc.get("id") or f"call_{i}"),
                name=str(fn.get("name") or ""),
                args=_parse_args(fn.get("arguments")),
            )
        )
    return calls


async def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 2048,
    timeout: int = 180,
    model: str = "auto",
) -> LLMResult:
    payload: dict[str, Any] = {
        "model": resolve_ov_model(model),
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base_url()}/v1/chat/completions", json=payload)
    except httpx.HTTPError as exc:
        raise LLMError(f"OpenVault unreachable ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        detail = resp.text[:400]
        raise LLMError(f"OpenVault HTTP {resp.status_code}: {detail}")
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = data.get("usage") or {}
    return LLMResult(
        text=str(message.get("content") or ""),
        tool_calls=_tools_from_choice(message),
        finish_reason=str(choice.get("finish_reason") or ""),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        model=str(data.get("model") or "openvault/auto"),
    )


_ENV_TO_PROVIDER: dict[str, str] = {
    "ANTHROPIC_API_KEY": "anthropic",
    "OPENROUTER_API_KEY": "openrouter",
    "DEEPSEEK_API_KEY": "deepseek",
    "OPENAI_API_KEY": "openai",
    "CURSOR_API_KEY": "custom",
    "XAI_API_KEY": "custom",
    "GROQ_API_KEY": "groq",
    "GOOGLE_API_KEY": "google",
    "CEREBRAS_API_KEY": "cerebras",
    "MISTRAL_API_KEY": "mistral",
}


def upsert_env_key(env_key: str, secret: str) -> dict[str, Any]:
    """Store one secret in OpenVault forever. Never logs the secret."""
    if os.environ.get("CREW_OPENVAULT", "1") == "0":
        return {"ok": False, "detail": "CREW_OPENVAULT=0"}
    secret = secret.strip()
    if not secret:
        return {"ok": False, "detail": "empty secret"}
    provider = _ENV_TO_PROVIDER.get(env_key, "custom")
    payload: dict[str, Any] = {
        "env_key": env_key,
        "secret": secret,
        "label": env_key,
    }
    if env_key == "CURSOR_API_KEY":
        payload["base_url"] = os.environ.get("CREW_CURSOR_BASE_URL", "https://api.cursor.com/v1")
        payload["provider"] = "custom"
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(f"{base_url()}/api/keyvault/upsert", json=payload)
        if resp.status_code >= 400:
            return {"ok": False, "detail": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        first = (data.get("results") or [{}])[0]
        key = first.get("key") or {}
        return {
            "ok": bool(data.get("ok") and first.get("ok")),
            "label": env_key,
            "id": key.get("id"),
            "provider": key.get("provider") or provider,
            "action": first.get("action"),
        }
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}"}


def push_env_keys() -> dict[str, Any]:
    """Copy any live process env keys into the vault. Values stay off logs."""
    if os.environ.get("CREW_OPENVAULT", "1") == "0":
        return {"ok": False, "skipped": True, "detail": "CREW_OPENVAULT=0"}
    pushed: list[str] = []
    errors: list[str] = []
    for env_key in _ENV_TO_PROVIDER:
        secret = os.environ.get(env_key, "").strip()
        if not secret:
            continue
        result = upsert_env_key(env_key, secret)
        if result.get("ok"):
            pushed.append(env_key)
        else:
            errors.append(f"{env_key}:{result.get('detail')}")
    return {"ok": not errors, "pushed": pushed, "errors": errors}


def ingest_cursor_from_files(root: Path | None = None) -> dict[str, Any]:
    """Find CURSOR_API_KEY on disk or env and vault it. Never returns the secret."""
    secret = os.environ.get("CURSOR_API_KEY", "").strip()
    source = "env" if secret else ""
    root = root or Path(os.environ.get("CREW_ROOT") or Path(__file__).resolve().parents[2])
    if not secret:
        keys_path = root / "data" / "crew" / "keys.json"
        if keys_path.is_file():
            try:
                blob = json.loads(keys_path.read_text(encoding="utf-8"))
                cand = blob.get("CURSOR_API_KEY") if isinstance(blob, dict) else None
                if isinstance(cand, str) and cand.strip():
                    secret, source = cand.strip(), "keys.json"
            except (OSError, ValueError):
                pass
    if not secret:
        for rel in (".env", ".env.local", "cursor.env"):
            path = root / rel
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("CURSOR_API_KEY="):
                    cand = stripped.split("=", 1)[1].strip().strip("'\"")
                    if cand:
                        secret, source = cand, rel
                        break
            if secret:
                break
    if not secret:
        return {"ok": False, "detail": "CURSOR_API_KEY not found on disk or env"}
    os.environ["CURSOR_API_KEY"] = secret
    result = upsert_env_key("CURSOR_API_KEY", secret)
    result["source"] = source
    result["chars"] = len(secret)
    return result


SEEDED_CORTEX_LABEL = "Netie Cortex (seeded)"


def disable_seeded_cortex_primary() -> dict[str, Any]:
    """Stop FreeRoute walking the dead Cortex seed key first (HTTP 404, non-retryable)."""
    if os.environ.get("CREW_OPENVAULT", "1") == "0":
        return {"ok": False, "detail": "CREW_OPENVAULT=0"}
    try:
        with httpx.Client(timeout=8.0) as client:
            listing = client.get(f"{base_url()}/api/keys")
            if listing.status_code != 200:
                return {"ok": False, "detail": f"list HTTP {listing.status_code}"}
            rows = (listing.json() or {}).get("keys") or []
            target = next(
                (row for row in rows if str(row.get("label") or "") == SEEDED_CORTEX_LABEL),
                None,
            )
            if target is None:
                return {"ok": True, "detail": "seeded cortex key not present"}
            if not target.get("enabled"):
                return {"ok": True, "id": target.get("id"), "detail": "already disabled"}
            resp = client.patch(
                f"{base_url()}/api/keys/{target['id']}",
                json={"enabled": False},
            )
        if resp.status_code >= 400:
            return {"ok": False, "detail": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        return {"ok": True, "id": target.get("id"), "detail": "disabled"}
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}"}
