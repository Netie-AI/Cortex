import time
from typing import Any

from netie.routing.adapters.base import AdapterResponse


def _to_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    md = getattr(obj, "model_dump", None)
    if callable(md):
        return md()
    if hasattr(obj, "__dict__"):
        return dict(vars(obj))
    return {}


def litellm_completion_to_adapter_response(resp: Any, t0: float) -> AdapterResponse:
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    raw = _to_dict(resp)

    choices = raw.get("choices") or []
    if not choices:
        content = ""
    else:
        ch0 = choices[0]
        if not isinstance(ch0, dict):
            ch0 = _to_dict(ch0)
        msg = ch0.get("message")
        if isinstance(msg, dict):
            content = msg.get("content") or ""
        elif msg is not None:
            content = getattr(msg, "content", None) or ""
        else:
            content = ch0.get("text") or ""

    usage = raw.get("usage")
    if usage is not None and not isinstance(usage, dict):
        usage = _to_dict(usage)
    usage = usage or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)

    return AdapterResponse(
        content=str(content),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=elapsed_ms,
        raw=raw,
    )
