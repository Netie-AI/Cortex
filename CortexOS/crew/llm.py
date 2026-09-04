"""Provider-agnostic chat completion over litellm (already a base dependency).

One entry point, :func:`chat`, used by every agent. The model string decides
the host (``anthropic/claude-sonnet-5``, ``openrouter/...``, ``deepseek/...``,
``openai/...`` with an optional base URL, ``ollama/...``) so the crew runs on
a Claude API key, a cheap API, any OpenAI-compatible gateway, or a local
model without code changes. litellm is imported lazily - tests fake this
module and never pay its import, and the server only pays it on first use.

Failures raise :class:`LLMError` with a human-readable reason; the runtime
persists that reason into the transcript instead of retrying another provider
behind the operator's back (KB R-0011: a silent fallback is a lie).

:func:`resolve_route` is the operator-visible router: a per-turn provider/model
pick wins, then a pinned ``CREW_PROVIDER``, then the configured chain. A miss
or a dead connector refuses with a reason. There is no walk to the next host.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    model: str = ""


@dataclass(frozen=True)
class Route:
    """One turn's host. ``connector`` is openvault or litellm; never both."""

    label: str
    model: str
    api_base: str | None
    source: str
    connector: str


_PROVIDER_ALIASES = {
    "openai": "openai-compatible",
    "ov": "openvault",
    "vault": "openvault",
    "google": "google",
    "gemini": "google",
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


def _connector_for(label: str) -> str:
    return "openvault" if label == "openvault" else "litellm"


def _refuse_unconfigured(row: Any, pick: str) -> None:
    if row.configured:
        return
    if row.label == "openvault":
        from CortexOS.crew.openvault import healthz

        detail = str(healthz().get("detail") or "not live")
        raise LLMError(f"OpenVault connector refused: {detail} (no silent fallback)")
    raise LLMError(
        f"provider '{pick}' is not configured ({row.source}); no silent fallback"
    )


def _assert_connector(row: Any) -> None:
    if row.label == "openvault":
        from CortexOS.crew.openvault import require_live

        require_live()
        return
    from CortexOS.crew.connectors import ConnectorError
    from CortexOS.crew.connectors import require as require_connector

    slug = "openai" if row.label == "openai-compatible" else row.label
    if slug in {"explicit", "ollama", "cursor"}:
        return
    try:
        require_connector(slug)
    except ConnectorError as exc:
        raise LLMError(str(exc)) from exc


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

    if pick:
        row = next((p for p in chain if p.label.lower() == pick), None)
        if row is None:
            known = ", ".join(p.label for p in chain)
            raise LLMError(f"unknown provider '{provider}' (known: {known}); no silent fallback")
        _refuse_unconfigured(row, pick)
        _assert_connector(row)
        chosen = model_s or row.model
        if row.label == "openvault" and not chosen.startswith("openvault/"):
            chosen = "openvault/" + chosen.removeprefix("openvault/")
        return Route(
            label=row.label,
            model=chosen,
            api_base=row.api_base,
            source=row.source,
            connector=_connector_for(row.label),
        )

    if model_s:
        if model_s.startswith("openvault/"):
            from CortexOS.crew.openvault import require_live

            require_live()
            return Route(
                label="openvault",
                model=model_s,
                api_base=None,
                source="turn-override",
                connector="openvault",
            )
        return Route(
            label="turn-override",
            model=model_s,
            api_base=None,
            source="turn-override",
            connector="litellm",
        )

    active = active_provider(chain)
    if active is None:
        raise LLMError(
            "no model provider configured (set CREW_PROVIDER / CREW_MODEL, an *_API_KEY,"
            " start OpenVault on :5000, or run Ollama); no silent fallback"
        )
    _assert_connector(active)
    return Route(
        label=active.label,
        model=active.model,
        api_base=active.api_base,
        source=active.source,
        connector=_connector_for(active.label),
    )


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


def _parse_args(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


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
        litellm = _litellm()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "num_retries": 1,
        }
        if tools:
            kwargs["tools"] = tools
        if api_base:
            kwargs["api_base"] = api_base
        if stream_cb is None:
            response = await litellm.acompletion(**kwargs)
            return _from_response(litellm, response, model)
        return await _streamed(litellm, kwargs, model, stream_cb)
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - every provider fails differently
        raise LLMError(f"model call failed ({model}): {type(exc).__name__}: {exc}") from exc


def _from_response(litellm: Any, response: Any, model: str) -> LLMResult:
    choice = response.choices[0]
    message = choice.message
    calls: list[ToolCall] = []
    for tc in getattr(message, "tool_calls", None) or []:
        calls.append(
            ToolCall(
                id=tc.id or f"call_{len(calls)}",
                name=tc.function.name or "",
                args=_parse_args(tc.function.arguments),
            )
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
    )


async def _streamed(
    litellm: Any,
    kwargs: dict[str, Any],
    model: str,
    stream_cb: Callable[[str], Awaitable[None]],
) -> LLMResult:
    text_parts: list[str] = []
    # tool-call fragments arrive keyed by index; args come as string shards
    pending: dict[int, dict[str, str]] = {}
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
        for tc in getattr(delta, "tool_calls", None) or []:
            slot = pending.setdefault(tc.index or 0, {"id": "", "name": "", "args": ""})
            if getattr(tc, "id", None):
                slot["id"] = tc.id
            fn = getattr(tc, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    slot["name"] += fn.name
                if getattr(fn, "arguments", None):
                    slot["args"] += fn.arguments

    calls = [
        ToolCall(id=slot["id"] or f"call_{i}", name=slot["name"], args=_parse_args(slot["args"]))
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
    )
