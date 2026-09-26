"""Env-direct model transport: provider keys from the process env, no OpenVault.

Opt-in only: ``CORTEX_MODEL_TRANSPORT=env-direct``. Unset (the default) keeps
OpenVault FreeRoute as the one model layer and this module does nothing.

When on, :mod:`CortexOS.integrations.freeroute` arms from the provider keys in
the process env and sends through :func:`request_json` here instead of
OpenVault. Everything above the transport (pick, measured route, validators,
journal) is unchanged. Custody is the operator's env, not the vault, so every
stamp says ``NOT OpenVault FreeRoute (env-direct)`` and the arming reason names
the keys it armed from. Nothing here falls back to another provider inside a
call: a refused call is a named refusal, and the picker routes around it.

Candidate ids are ``<provider>:<model>`` (``google:gemini-3-flash-preview``).
Each provider speaks the OpenAI chat-completions wire.

Hardening (security review of b75b3b5):

- The opt-in latches at its first read in the process (:func:`enabled`). A
  later ``os.environ`` write (an OpenVault keyvault snapshot, a plugin) cannot
  flip custody either way; :func:`reset_opt_in` is the test/restart hook.
- Only in-process callers spend the operator's env keys. A relayed HTTP
  caller is refused (:data:`RELAY_REFUSED`) by the freeroute layer.
- Redirects are refused, not followed (urllib would forward the provider key
  to the redirect target); the response body is read to at most
  :data:`MAX_RESPONSE_BYTES` and within a total deadline.
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

TRANSPORT_ENV = "CORTEX_MODEL_TRANSPORT"
IMPL = "env-direct"
CUSTODY = "process-env (operator opt-in, not OpenVault)"
URL = "env-direct:"

RELAY_REFUSED = "env-direct serves in-process callers only; relayed callers are refused"
CLOUD_ONLY = "env-direct is cloud-only"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_READ_CHUNK = 64 * 1024


@dataclass(frozen=True)
class Provider:
    label: str
    key_envs: tuple[str, ...]
    base: str
    models: tuple[str, ...]

    @property
    def models_env(self) -> str:
        return f"CORTEX_DIRECT_{self.label.upper()}_MODELS"


# Order is the default exploration order. Defaults are models that answered a
# live chat on 2026-09-25; override per provider with CORTEX_DIRECT_<P>_MODELS.
PROVIDERS: tuple[Provider, ...] = (
    Provider(
        "google",
        ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "https://generativelanguage.googleapis.com/v1beta/openai",
        ("gemini-3-flash-preview",),
    ),
    Provider(
        "nvidia",
        ("NVIDIA_API_KEY",),
        "https://integrate.api.nvidia.com/v1",
        ("moonshotai/kimi-k3",),
    ),
    Provider(
        "mistral",
        ("MISTRAL_API_KEY",),
        "https://api.mistral.ai/v1",
        ("mistral-medium-latest",),
    ),
    Provider(
        "cerebras",
        ("CEREBRAS_API_KEY",),
        "https://api.cerebras.ai/v1",
        ("gpt-oss-120b",),
    ),
)
_BY_LABEL = {p.label: p for p in PROVIDERS}
#: Every env name a provider key is read from. Child processes never inherit these.
KEY_ENVS: tuple[str, ...] = tuple(dict.fromkeys(n for p in PROVIDERS for n in p.key_envs))

_latch_lock = threading.Lock()
_latched: bool | None = None


def _read_opt_in(src: Mapping[str, str]) -> bool:
    return (src.get(TRANSPORT_ENV) or "").strip().lower() == IMPL


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """Is the operator opt-in on? Process env is latched at its first read.

    A runtime ``os.environ`` write after that (e.g. a hydrated keyvault
    snapshot) does not change custody. ``env`` given: evaluated, not latched.
    """
    global _latched
    if env is not None:
        return _read_opt_in(env)
    with _latch_lock:
        if _latched is None:
            _latched = _read_opt_in(os.environ)
        return _latched


def reset_opt_in() -> None:
    """Forget the latched opt-in; the next :func:`enabled` re-reads the env."""
    global _latched
    with _latch_lock:
        _latched = None


def _key(p: Provider, env: Mapping[str, str]) -> tuple[str, str]:
    for name in p.key_envs:
        value = (env.get(name) or "").strip()
        if value:
            return value, name
    return "", ""


def _models(p: Provider, env: Mapping[str, str]) -> tuple[str, ...]:
    raw = [m.strip() for m in (env.get(p.models_env) or "").split(",") if m.strip()]
    return tuple(dict.fromkeys(raw)) if raw else p.models


def configured(env: Mapping[str, str] | None = None) -> list[tuple[Provider, str, tuple[str, ...]]]:
    """(provider, key env name, models) for each provider with a key set."""
    src = os.environ if env is None else env
    out = []
    for p in PROVIDERS:
        _, name = _key(p, src)
        if name:
            out.append((p, name, _models(p, src)))
    return out


def split_id(candidate: str) -> tuple[Provider | None, str]:
    label, sep, model = (candidate or "").partition(":")
    if not sep or not model:
        return None, ""
    return _BY_LABEL.get(label.strip().lower()), model.strip()


def _error(status: int, message: str, kind: str = "env_direct_refused") -> tuple[int, dict[str, Any]]:
    return status, {"error": {"message": message, "type": kind}}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: urllib would resend the provider key to the target."""

    def redirect_request(  # noqa: PLR0913 - stdlib signature
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _urlopen(req: urllib.request.Request, timeout: float) -> Any:
    """The one network call. No redirects followed. Tests replace this."""
    return _OPENER.open(req, timeout=timeout)


def _now() -> float:
    return time.monotonic()


class _Refused(Exception):
    """A named refusal raised while reading a response."""


def _read_bounded(resp: Any, deadline: float) -> bytes:
    """Read at most ``MAX_RESPONSE_BYTES`` before ``deadline``; else a named refusal."""
    read = getattr(resp, "read1", None) or resp.read
    chunks: list[bytes] = []
    size = 0
    while True:
        if _now() > deadline:
            raise _Refused("total deadline exceeded while reading the response")
        chunk = read(_READ_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise _Refused(f"response larger than {MAX_RESPONSE_BYTES} bytes refused")
        chunks.append(chunk)
    return b"".join(chunks)


def request_json(
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 45.0,
    base: str | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """Same shape as ``openvault_client.request_json``. Never raises.

    ``headers`` is ignored on purpose: it carries the OpenVault bearer, which
    must never reach a third-party host. The provider key comes from the env.
    """
    _ = headers, base
    if method.upper() != "POST" or path != "/v1/chat/completions":
        return _error(400, f"env-direct serves POST /v1/chat/completions only, not {method} {path}")
    payload = dict(body or {})
    requested = str(payload.get("model") or "")
    provider, model = split_id(requested)
    if provider is None:
        known = ", ".join(p.label for p in PROVIDERS)
        return _error(400, f"env-direct model id must be <provider>:<model> with provider in {known}; got {requested!r}")
    key, _name = _key(provider, os.environ)
    if not key:
        return _error(401, f"env-direct: no key in env for {provider.label} ({' or '.join(provider.key_envs)})")
    payload["model"] = model
    payload.pop("metadata", None)
    for extra in ("local_only",):
        payload.pop(extra, None)
    req = urllib.request.Request(
        f"{provider.base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            # Cloudflare-fronted hosts (Cerebras) 403 the default Python-urllib agent.
            "User-Agent": "cortex-env-direct/1",
        },
    )
    deadline = _now() + float(timeout)
    try:
        with _urlopen(req, timeout) as resp:
            status, data = int(resp.status), _json(_read_bounded(resp, deadline))
    except urllib.error.HTTPError as exc:
        if 300 <= int(exc.code) < 400:
            _close(exc)
            return _error(
                int(exc.code),
                f"env-direct: {provider.label} answered a redirect (HTTP {exc.code}); "
                "redirects are refused, the provider key is never re-sent",
            )
        try:
            raw = _read_bounded(exc, deadline)
        except _Refused as why:
            return _error(0, f"env-direct: {provider.label} {why}")
        except (OSError, http.client.HTTPException, ValueError):
            raw = b""
        status, data = int(exc.code), _json(raw)
    except _Refused as why:
        return _error(0, f"env-direct: {provider.label} {why}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
        return 0, {"error": {"message": f"{provider.label} unreachable: {type(exc).__name__}"}}
    if status != 200 and isinstance(data, dict) and "error" not in data:
        # Mistral puts the reason at top level; the stamp reads ``error.message``.
        msg = data.get("message") or data.get("detail") or data.get("title")
        if msg:
            data = {"error": {"message": str(msg), "type": str(data.get("type") or "")}}
    if status == 200 and isinstance(data, dict):
        served = str(data.get("model") or model)
        # Served id stays namespaced by the host Cortex chose, so the measured
        # route scores the provider+model pair that actually answered.
        data["model"] = f"{provider.label}:{served}"
        data["served_provider"] = provider.label
        data["served_model"] = served
        data["served_local"] = False
    return status, data


def _close(exc: urllib.error.HTTPError) -> None:
    try:
        exc.close()
    except Exception:  # noqa: BLE001 - closing a refused response must not raise
        pass


def _json(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace") or "null")
    except ValueError:
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]  # Google wraps errors in a one-element list.
    return data if isinstance(data, dict) else None


__all__ = [
    "CLOUD_ONLY",
    "CUSTODY",
    "IMPL",
    "KEY_ENVS",
    "MAX_RESPONSE_BYTES",
    "PROVIDERS",
    "Provider",
    "RELAY_REFUSED",
    "TRANSPORT_ENV",
    "URL",
    "configured",
    "enabled",
    "request_json",
    "reset_opt_in",
    "split_id",
]
