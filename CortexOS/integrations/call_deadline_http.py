"""httpx transport for one OpenVault call that a client disconnect can close.

``freeroute`` loads this by name only while ``CORTEX_CALL_DEADLINE`` is on and
a cancel event is bound. It is not a static import of FreeRoute: the plan+SQL
contract forbids ``httpx`` on that graph. The JSON bytes match the stdlib client.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from typing import Any


def _note_headers(headers: Mapping[str, str] | None) -> None:
    from CortexOS.integrations.freeroute import note_response_headers

    if not headers:
        note_response_headers({})
        return
    note_response_headers({str(key): str(value) for key, value in headers.items()})


def _json_dict(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def request_httpx(
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 5.0,
    base: str | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """One OpenVault JSON call. Closes the client when the bound caller leaves."""
    import httpx

    from CortexOS.integrations.call_deadline import current_cancel
    from CortexOS.integrations.openvault_client import is_loopback_url, openvault_base_url

    root = base or openvault_base_url()
    url = f"{root}{path}"
    payload = json.dumps(dict(body)).encode("utf-8") if body is not None else None
    sent = {"Accept": "application/json"}
    if payload is not None:
        sent["Content-Type"] = "application/json"
    sent.update(dict(headers or {}))
    cancel = current_cancel()
    client = httpx.Client(timeout=httpx.Timeout(timeout), trust_env=not is_loopback_url(root))
    done = threading.Event()

    def _abort() -> None:
        if cancel is None:
            return
        while not done.is_set():
            if cancel.is_set():
                client.close()
                return
            done.wait(0.05)

    closer = threading.Thread(target=_abort, name="cortex-call-cancel", daemon=True)
    closer.start()
    try:
        try:
            resp = client.request(method.upper(), url, content=payload, headers=sent)
        except httpx.HTTPError:
            return 0, None
        _note_headers(resp.headers)
        return int(resp.status_code), _json_dict(resp.content)
    finally:
        done.set()
        client.close()
        closer.join(timeout=1.0)
