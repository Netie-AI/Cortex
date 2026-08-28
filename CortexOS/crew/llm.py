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
