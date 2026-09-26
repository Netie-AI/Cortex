"""Provider-agnostic chat completion over litellm (already a base dependency).

One entry point, :func:`chat`, used by every agent. The model string decides
the host (``anthropic/claude-sonnet-5``, ``openrouter/...``, ``deepseek/...``,
``gemini/...``, ``nvidia_nim/...``, ``openai/...`` with an optional base URL,
``ollama/...``) so the crew runs on a Claude API key, a cheap API, any
OpenAI-compatible gateway, or a local model without code changes. litellm is
imported lazily - tests fake this module and never pay its import, and the
server only pays it on first use.

Failures raise :class:`LLMError` with a human-readable reason; the runtime
persists that reason into the transcript instead of retrying another provider
behind the operator's back (KB R-0011: a silent fallback is a lie).

:func:`resolve_route` is the operator-visible router: a per-turn provider/model
pick wins, then a pinned ``CREW_PROVIDER``, then the configured chain. A miss
or a dead connector refuses with a reason. There is no walk to the next host.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from CortexOS.integrations import pii_mask


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]
    #: Set when the model sent arguments that are not a JSON object. The
    #: runtime returns this to the model instead of running the tool with {}.
    args_error: str = ""


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    model: str = ""
    #: The host's separate reasoning channel (``reasoning_content``), if any.
    #: Kimi on NIM sometimes ends a turn with text only here and no content.
    reasoning: str = ""


@dataclass(frozen=True)
class Route:
    """One turn's host. ``connector`` is openvault or litellm; never both."""

    label: str
    model: str
    api_base: str | None
    source: str
    connector: str
    armed_via: str = ""

    def as_public(self) -> dict[str, object]:
        return {
            "label": self.label,
            "model": self.model,
            "source": self.source,
            "connector": self.connector,
            "armed_via": self.armed_via,
        }


_PROVIDER_ALIASES = {
    "openai": "openai-compatible",
    "ov": "openvault",
    "vault": "openvault",
    "google": "google",
    "gemini": "google",
    "nvidia_nim": "nvidia",
    "nim": "nvidia",
}

_MODEL_PREFIX_TO_LABEL = {
    "openvault": "openvault",
    "anthropic": "anthropic",
    "openrouter": "openrouter",
    "deepseek": "deepseek",
    "openai": "openai-compatible",
    "xai": "xai",
    "groq": "groq",
    "gemini": "google",
    "google": "google",
    # "nvidia/..." is a NIM model namespace, not a litellm prefix; only the
    # litellm prefix names the host.
    "nvidia_nim": "nvidia",
    "cerebras": "cerebras",
    "mistral": "mistral",
    "ollama": "ollama",
}


def _empty_usage() -> dict[str, Any]:
    return {
        "llm_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": 0.0,
        "by_route": {},
    }


_USAGE: dict[str, Any] = _empty_usage()


def reset_usage() -> None:
    """Test hook. Production HUD reads the live process totals."""
    global _USAGE
    _USAGE = _empty_usage()


def usage_snapshot() -> dict[str, Any]:
    routes = {
        name: dict(slot) for name, slot in (_USAGE.get("by_route") or {}).items()
    }
    return {
        "llm_calls": int(_USAGE.get("llm_calls") or 0),
        "prompt_tokens": int(_USAGE.get("prompt_tokens") or 0),
        "completion_tokens": int(_USAGE.get("completion_tokens") or 0),
        "cost_usd": float(_USAGE.get("cost_usd") or 0.0),
        "by_route": routes,
    }


def usage_view(stored: dict[str, Any] | None = None) -> dict[str, Any]:
    """HUD payload: durable store totals win; session fills a fresh process."""
    session = usage_snapshot()
    stored = dict(stored or {})
    calls = int(stored.get("llm_calls") or 0) or session["llm_calls"]
    prompt = int(stored.get("prompt_tokens") or 0) or session["prompt_tokens"]
    completion = int(stored.get("completion_tokens") or 0) or session["completion_tokens"]
    cost = float(stored.get("cost_usd") or 0.0) or session["cost_usd"]
    by_route = stored.get("by_route") or session["by_route"]
    return {
        "llm_calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cost_usd": cost,
        "tokens": prompt + completion,
        "by_route": by_route,
        "session": session,
        "stored": stored,
    }


def record_usage(result: LLMResult, *, route: str = "") -> dict[str, Any]:
    name = (route or result.model or "unknown").strip() or "unknown"
    _USAGE["llm_calls"] = int(_USAGE.get("llm_calls") or 0) + 1
    _USAGE["prompt_tokens"] = int(_USAGE.get("prompt_tokens") or 0) + int(result.prompt_tokens or 0)
    _USAGE["completion_tokens"] = int(_USAGE.get("completion_tokens") or 0) + int(
        result.completion_tokens or 0
    )
    _USAGE["cost_usd"] = round(
        float(_USAGE.get("cost_usd") or 0.0) + float(result.cost_usd or 0.0), 6
    )
    slots: dict[str, Any] = _USAGE.setdefault("by_route", {})
    slot = slots.setdefault(
        name,
        {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
    )
    slot["llm_calls"] += 1
    slot["prompt_tokens"] += int(result.prompt_tokens or 0)
    slot["completion_tokens"] += int(result.completion_tokens or 0)
    slot["cost_usd"] = round(float(slot["cost_usd"] or 0.0) + float(result.cost_usd or 0.0), 6)
    return usage_snapshot()


def _rewrite_grok_fast(model: str) -> str:
    raw = (model or "").strip()
    if "grok" in raw.lower() and "fast" in raw.lower():
        return "openai/grok-4.6"
    return raw


def _norm_provider(label: str) -> str:
    raw = (label or "").strip().lower()
    return _PROVIDER_ALIASES.get(raw, raw)


def _label_for_model(model: str) -> str:
    raw = (model or "").strip()
    if not raw:
        return ""
    if raw.startswith("openvault/"):
        return "openvault"
    if "grok" in raw.lower():
        return "cursor"
    prefix = raw.split("/", 1)[0].lower()
    return _MODEL_PREFIX_TO_LABEL.get(prefix, "")


def _connector_for(row: Any) -> str:
    conn = getattr(row, "connector", None)
    if conn:
        return str(conn)
    return "openvault" if row.label == "openvault" else "litellm"


def _refuse_unconfigured(row: Any, pick: str) -> None:
    if row.configured:
        return
    if row.label == "openvault":
        from CortexOS.crew.freeroute import arming as freeroute_arming

        detail = str(freeroute_arming().get("detail") or "not armed")
        raise LLMError(f"OpenVault connector refused: {detail} (no silent fallback)")
    raise LLMError(
        f"provider '{pick}' is not configured ({row.source or 'unarmed'}); no silent fallback"
    )


def _assert_connector(row: Any) -> None:
    connector = _connector_for(row)
    if connector == "openvault" or row.label == "openvault":
        from CortexOS.crew.openvault import require_live

        require_live()
        return
    from CortexOS.crew.connectors import ConnectorError
    from CortexOS.crew.connectors import require as require_connector

    slug = "openai" if row.label == "openai-compatible" else row.label
    if slug in {"explicit", "ollama"}:
        return
    try:
        require_connector(slug)
    except ConnectorError as exc:
        raise LLMError(str(exc)) from exc


def _route_from_row(row: Any, model_s: str) -> Route:
    connector = _connector_for(row)
    chosen = model_s or row.model
    if connector == "openvault" and not chosen.startswith("openvault/"):
        if row.label == "cursor" and not model_s:
            from CortexOS.crew.openvault import cursor_model

            chosen = "openvault/" + cursor_model()
        else:
            chosen = "openvault/" + chosen.removeprefix("openvault/")
    return Route(
        label=row.label,
        model=chosen,
        api_base=None if connector == "openvault" else row.api_base,
        source=row.source,
        connector=connector,
        armed_via=str(getattr(row, "armed_via", "") or ""),
    )


def resolve_route(*, provider: str | None = None, model: str | None = None) -> Route:
    """Pick the host for this turn. Operator input wins. Never falls through.

    ``provider`` is a chain label (anthropic, openvault, groq, ...). ``model`` is
    an optional litellm / OpenVault model string. A selected host that is unset
    or whose connector is down raises :class:`LLMError` with the reason.
    """
    from CortexOS.crew.config import active_provider, resolve_providers

    pick = _norm_provider(provider or "")
    model_s = _rewrite_grok_fast(model or "")
    chain = resolve_providers()

    if not pick and model_s:
        inferred = _label_for_model(model_s)
        if inferred:
            pick = inferred
        else:
            explicit = next((p for p in chain if p.label == "explicit" and p.configured), None)
            if explicit is not None and model_s == explicit.model:
                pick = "explicit"
            else:
                raise LLMError(f"unarmed model '{model_s}' (no silent fallback)")

    if pick:
        row = next((p for p in chain if p.label.lower() == pick), None)
        if row is None:
            known = ", ".join(p.label for p in chain)
            raise LLMError(f"unknown provider '{provider}' (known: {known}); no silent fallback")
        _refuse_unconfigured(row, pick)
        _assert_connector(row)
        return _route_from_row(row, model_s)

    active = active_provider(chain)
    if active is None:
        raise LLMError(
            "no model provider configured (set CREW_PROVIDER / CREW_MODEL, an *_API_KEY,"
            " start OpenVault on :5000, or run Ollama); no silent fallback"
        )
    _assert_connector(active)
    return _route_from_row(active, "")


def chosen_public(
    *, provider: str | None = None, model: str | None = None
) -> dict[str, object]:
    """HUD/API snapshot of the host this turn would use. Null chosen if unarmed."""
    try:
        route = resolve_route(provider=provider, model=model)
        return {"chosen": route.as_public(), "refused": None}
    except LLMError as exc:
        return {"chosen": None, "refused": str(exc)}


_configured = False


def _litellm() -> Any:
    global _configured
    import litellm

    if not _configured:
        litellm.telemetry = False
        litellm.drop_params = True  # tolerate provider-specific params quietly
        litellm.suppress_debug_info = True
        _configured = True
    return litellm


def _api_key(model: str) -> str | None:
    """The env key the chain stamped for this host, handed to litellm.

    Only hosts in ``keys.KEY_PREFIXES`` are listed: litellm would otherwise
    read a different env name (or none) than the one the chain named. Every
    other host keeps litellm's own env lookup. The value goes to litellm only,
    never into a result, a route or an error.
    """
    from CortexOS.crew.keys import KEY_PREFIXES, key_env

    label = KEY_PREFIXES.get(str(model).split("/", 1)[0].lower())
    name = key_env(label) if label else ""
    return os.environ[name].strip() if name else None


def _parse_args(raw: str | None) -> dict[str, Any]:
    return _parse_args_checked(raw)[0]


def _parse_args_checked(raw: Any) -> tuple[dict[str, Any], str]:
    """Tool arguments as a dict plus a reason when they were not usable.

    Models sometimes wrap the JSON in a code fence or send a trailing comma.
    Unwrapping a fence is lossless; anything else that is not a JSON object is
    reported back, because running the tool with ``{}`` silently does the
    wrong thing (reads '.', searches nothing) and the model never learns why.
    """
    if isinstance(raw, dict):
        return raw, ""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}, ""
    text = str(raw).strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError) as exc:
        return {}, f"arguments were not valid JSON ({exc}): {str(raw)[:200]}"
    if not isinstance(parsed, dict):
        return {}, f"arguments must be a JSON object, got {type(parsed).__name__}"
    return parsed, ""


def _tool_call(call_id: str, name: str, raw: Any) -> ToolCall:
    args, err = _parse_args_checked(raw)
    return ToolCall(id=call_id, name=name, args=args, args_error=err)


# Same-host retry on a provider rate limit. This is not a fallback: the same
# route and model are asked again after a pause, and the final error says how
# many attempts were made. Only raised before any text has streamed.
# Measured 2026-09-25 on a shared NVIDIA NIM key: two retries over ~11s still
# lost 4 of 15 eval turns to 429; the per-minute window needs ~40s of patience.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF_S = (3.0, 10.0, 25.0)


def _strip_cache_control(model: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop ``cache_control`` markers for Gemini AI Studio.

    litellm turns the marker into an explicit ``cachedContents`` create, which
    the free tier refuses outright (HTTP 429 "TotalCachedContentStorageTokens
    PerModelFreeTier limit=0", measured 2026-09-25): every turn failed before
    the model ran. Gemini already caches a repeated prefix implicitly, so the
    marker buys nothing there. Other hosts keep it.
    """
    if not str(model).startswith("gemini/"):
        return messages
    if not any("cache_control" in m for m in messages):
        return messages
    return [{k: v for k, v in m.items() if k != "cache_control"} for m in messages]


def _is_hard_quota(exc: BaseException) -> bool:
    """A 429 that seconds of backoff cannot clear: a zero quota, or a per-day
    quota that is spent (Gemini free tier: ``GenerateRequestsPerDay...``).
    Retrying those only adds ~40s before the same refusal."""
    text = " ".join(str(exc).split()).lower()
    return (
        "limit=0," in text
        or "limit: 0," in text
        or "limit=0 " in text
        or "perday" in text
    )


def _is_rate_limited(exc: BaseException) -> bool:
    if type(exc).__name__ == "RateLimitError":
        return True
    status = getattr(exc, "status_code", None)
    return status == 429


def _describe_failure(exc: BaseException) -> str:
    """One readable reason. Provider bodies are long JSON; keep the head."""
    status = getattr(exc, "status_code", None)
    kind = type(exc).__name__
    hint = ""
    if _is_rate_limited(exc):
        hint = "rate limited or out of quota (HTTP 429)"
    elif status == 401 or kind == "AuthenticationError":
        hint = "key rejected (HTTP 401)"
    elif status == 402:
        hint = "payment required (HTTP 402)"
    elif status == 404 or kind == "NotFoundError":
        hint = "model not found on this host (HTTP 404)"
    elif kind in {"Timeout", "APITimeoutError"} or isinstance(exc, TimeoutError):
        hint = "timed out"
    detail = " ".join(str(exc).split())[:300]
    return f"{kind}: {hint + ' - ' if hint else ''}{detail}"


async def chat(
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    api_base: str | None = None,
    max_tokens: int = 4096,
    timeout: int = 180,
    stream_cb: Callable[[str], Awaitable[None]] | None = None,
) -> LLMResult:
    try:
        if str(model).startswith("openvault/"):
            from CortexOS.crew import openvault as ov

            return await ov.chat(
                messages, tools=tools, max_tokens=max_tokens, timeout=timeout, model=model
            )
        # Cortex #268: mask PII before litellm sends anything; fail closed.
        try:
            masked = pii_mask.mask_messages(messages)
        except pii_mask.MaskingFailed as exc:
            raise LLMError(
                f"model call refused ({model}): {pii_mask.REFUSED_REASON}, nothing sent"
            ) from exc
        restore = masked.restore
        litellm = _litellm()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": _strip_cache_control(model, masked.messages),
            "max_tokens": max_tokens,
            "timeout": timeout,
            "num_retries": 1,
        }
        if tools:
            kwargs["tools"] = tools
        if api_base:
            kwargs["api_base"] = api_base
        key = _api_key(model)
        if key:
            kwargs["api_key"] = key
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - every provider fails differently
        raise LLMError(f"model call failed ({model}): {_describe_failure(exc)}") from exc

    emitted = False

    async def _cb(text: str) -> None:
        nonlocal emitted
        emitted = True
        assert stream_cb is not None
        # Best effort per chunk: a placeholder split across chunks shows as-is
        # locally; the final LLMResult is always fully restored.
        await stream_cb(pii_mask.restore_text(text, restore))

    attempt = 0
    while True:
        attempt += 1
        try:
            if stream_cb is None:
                response = await litellm.acompletion(**kwargs)
                return _restored(_from_response(litellm, response, model), restore)
            return _restored(await _streamed(litellm, kwargs, model, _cb), restore)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - every provider fails differently
            if (
                _is_rate_limited(exc)
                and not _is_hard_quota(exc)
                and not emitted
                and attempt <= RATE_LIMIT_RETRIES
            ):
                await asyncio.sleep(RATE_LIMIT_BACKOFF_S[min(attempt, len(RATE_LIMIT_BACKOFF_S)) - 1])
                continue
            tries = f" after {attempt} attempts" if attempt > 1 else ""
            raise LLMError(
                f"model call failed ({model}){tries}: {_describe_failure(exc)}"
            ) from exc


def _restored(result: LLMResult, restore: dict[str, str]) -> LLMResult:
    """Put this call's masked PII back into the reply (Cortex #268), locally."""
    if not restore:
        return result
    result.text = pii_mask.restore_text(result.text, restore)
    result.reasoning = pii_mask.restore_text(result.reasoning, restore)
    for call in result.tool_calls:
        call.args = pii_mask.restore_value(call.args, restore)
    return result


def _from_response(litellm: Any, response: Any, model: str) -> LLMResult:
    choice = response.choices[0]
    message = choice.message
    calls: list[ToolCall] = []
    for tc in getattr(message, "tool_calls", None) or []:
        calls.append(
            _tool_call(tc.id or f"call_{len(calls)}", tc.function.name or "", tc.function.arguments)
        )
    usage = getattr(response, "usage", None)
    cost: float | None = None
    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception:  # noqa: BLE001 - unknown local models have no price sheet
        cost = None
    return LLMResult(
        text=message.content or "",
        tool_calls=calls,
        finish_reason=choice.finish_reason or "",
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        cost_usd=cost,
        model=model,
        reasoning=str(getattr(message, "reasoning_content", None) or ""),
    )


async def _streamed(
    litellm: Any,
    kwargs: dict[str, Any],
    model: str,
    stream_cb: Callable[[str], Awaitable[None]],
) -> LLMResult:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    # tool-call fragments arrive keyed by index; args come as string shards
    pending: dict[int, dict[str, str]] = {}
    alias: dict[int | None, int] = {}
    finish = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    stream = await litellm.acompletion(
        **kwargs, stream=True, stream_options={"include_usage": True}
    )
    async for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", prompt_tokens)
            completion_tokens = getattr(usage, "completion_tokens", completion_tokens)
        if not getattr(chunk, "choices", None):
            continue
        choice = chunk.choices[0]
        finish = choice.finish_reason or finish
        delta = choice.delta
        if getattr(delta, "content", None):
            text_parts.append(delta.content)
            await stream_cb(delta.content)
        thought = getattr(delta, "reasoning_content", None)
        if isinstance(thought, str) and thought:
            reasoning_parts.append(thought)
        for tc in getattr(delta, "tool_calls", None) or []:
            raw_idx = getattr(tc, "index", None)
            new_id = getattr(tc, "id", None) or ""
            # Some hosts omit the index or stamp every parallel call index 0.
            # A fresh id on an occupied slot is a fresh call; chunks without
            # an id continue whichever call that raw index last pointed at.
            # Otherwise two calls merge into "web_searchweb_search" / "{..}{..}".
            key = alias.get(raw_idx, raw_idx if raw_idx is not None else 0)
            if new_id and key in pending and pending[key]["id"] not in {"", new_id}:
                key = max(pending) + 1
            alias[raw_idx] = key
            slot = pending.setdefault(key, {"id": "", "name": "", "args": ""})
            if new_id:
                slot["id"] = new_id
            fn = getattr(tc, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    slot["name"] += fn.name
                if getattr(fn, "arguments", None):
                    slot["args"] += fn.arguments

    calls = [
        _tool_call(slot["id"] or f"call_{i}", slot["name"], slot["args"])
        for i, slot in sorted(pending.items())
    ]
    return LLMResult(
        text="".join(text_parts),
        tool_calls=calls,
        finish_reason=finish,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=None,
        model=model,
        reasoning="".join(reasoning_parts),
    )
