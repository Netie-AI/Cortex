"""One model step for DMS brain handlers.

Every call goes through ``CortexOS.crew.freeroute.complete``. Unarmed, no
OpenVault route, or a transport failure is the named refusal
``model_route_unavailable``. This module does not import a provider SDK and
does not log the prompt.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import threading
from typing import Any

MODEL_ROUTE_UNAVAILABLE = "model_route_unavailable"


def refusal_body() -> dict[str, Any]:
    """Fail-closed body. No prose that could be read as a model answer."""
    return {
        "ok": False,
        "refusal": MODEL_ROUTE_UNAVAILABLE,
        "llm_used": False,
        "served_provider": None,
        "served_model": None,
    }


def served_from(result: dict[str, Any]) -> tuple[Any, Any]:
    """Copy served_* from the FreeRoute stamp.

    A requested model id, ``stamp.served``, or a field inside the model text
    is not a substitute. Missing fields stay None.
    """
    stamp = result.get("stamp")
    if isinstance(stamp, dict) and (
        "served_provider" in stamp or "served_model" in stamp
    ):
        return stamp.get("served_provider"), stamp.get("served_model")
    if "served_provider" in result or "served_model" in result:
        return result.get("served_provider"), result.get("served_model")
    return None, None


def parse_model_json(text: str) -> Any:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw)


def _drive(invoke: Any) -> Any:
    """Run ``invoke()`` and await it when FreeRoute's ``complete`` is async."""

    async def _call() -> Any:
        out = invoke()
        if inspect.isawaitable(out):
            return await out
        return out

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_call())

    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["result"] = asyncio.run(_call())
        except BaseException as exc:  # noqa: BLE001 - hop the error back to the caller
            box["error"] = exc

    thread = threading.Thread(target=_worker, name="dms-brain-freeroute")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def call_freeroute(
    messages: list[dict[str, Any]],
    *,
    purpose: str = "think",
) -> dict[str, Any]:
    """One ``CortexOS.crew.freeroute.complete`` call. Never raises."""
    try:
        from CortexOS.crew import freeroute
    except Exception:  # noqa: BLE001 - a missing route is a refusal, not a provider call
        return {"ok": False}

    try:
        result = _drive(lambda: freeroute.complete(messages, purpose=purpose))
    except Exception:  # noqa: BLE001 - do not surface prompt text from the client
        return {"ok": False}
    if not isinstance(result, dict):
        return {"ok": False}
    return result
