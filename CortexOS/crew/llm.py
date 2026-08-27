"""Minimal OpenAI-compatible chat client — stdlib only, local-first.

Talks to any /v1/chat/completions endpoint (Ollama, LM Studio, llama.cpp,
vLLM, OpenRouter). Streaming is parsed from SSE lines; tool-call deltas are
assembled the way the OpenAI wire format fragments them. No LiteLLM, no keys
required for local backends.

Failure is loud: a dead backend raises ``LLMError`` and the runtime turns that
into a visible notice in the transcript — never a silent fallback (R-0011).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

OLLAMA_BASE = "http://127.0.0.1:11434/v1"
GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_MODEL = "gemini-3.5-flash"


def resolve_base() -> str:
    if os.environ.get("CREW_LLM_BASE"):
        return os.environ["CREW_LLM_BASE"]
    if os.environ.get("GEMINI_API_KEY"):
        return GEMINI_OPENAI_BASE
    return OLLAMA_BASE


def resolve_model() -> str:
    if os.environ.get("CREW_LLM_MODEL"):
        return os.environ["CREW_LLM_MODEL"]
    if os.environ.get("GEMINI_API_KEY"):
        return GEMINI_MODEL
    return "qwen3:4b"


def resolve_api_key() -> str:
    return os.environ.get("CREW_LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""


DEFAULT_BASE = OLLAMA_BASE
DEFAULT_MODEL = "qwen3:4b"


class LLMError(RuntimeError):
    pass


def accumulate_stream_events(
    lines: list[str], on_delta: Callable[[str], None] | None = None
) -> dict[str, Any]:
    """Fold OpenAI-format SSE ``data:`` lines into one completed message.

    Pure so it is testable without a live backend. Tool-call fragments arrive
    as {index, id?, function:{name?, arguments-chunk}} and are keyed by index.
    """
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, int] = {}
    finish_reason: str | None = None

    for raw in lines:
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        if chunk.get("usage"):
            u = chunk["usage"]
            usage = {
                "prompt_tokens": int(u.get("prompt_tokens") or 0),
                "completion_tokens": int(u.get("completion_tokens") or 0),
            }
        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
                if on_delta is not None:
                    on_delta(piece)
            for tc in delta.get("tool_calls") or []:
                idx = int(tc.get("index") or 0)
                slot = tool_calls.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""}
                )
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

    calls = []
    for idx in sorted(tool_calls):
        slot = tool_calls[idx]
        try:
            args = json.loads(slot["arguments"]) if slot["arguments"] else {}
        except ValueError:
            args = {"_raw": slot["arguments"]}
        calls.append({"id": slot["id"] or f"call_{idx}", "name": slot["name"], "arguments": args})

    return {
        "content": "".join(content_parts),
        "tool_calls": calls,
        "usage": usage,
        "finish_reason": finish_reason,
    }


def chat_completion(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    on_delta: Callable[[str], None] | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Blocking streamed completion — call via ``asyncio.to_thread``."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    api_key = resolve_api_key()
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            lines = [ln.decode("utf-8", "replace") for ln in resp]
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode("utf-8", "replace")
        raise LLMError(f"model backend {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"model backend unreachable at {base_url}: {exc}") from exc
    return accumulate_stream_events(lines, on_delta=on_delta)


def list_models(base_url: str, timeout: float = 5.0) -> list[str] | None:
    """Model ids from /v1/models. ``None`` means unreachable — an empty list
    means the backend is up with nothing pulled; the two must not blur."""
    req = urllib.request.Request(base_url.rstrip("/") + "/models")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    return [m.get("id", "") for m in data.get("data") or [] if m.get("id")]
