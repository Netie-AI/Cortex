"""Secret hygiene (PRD EPIC-HARNESS-ENT R1.4, R1.6, R5.4). Stdlib only.

* :func:`secret_env_names` is the one list of secret env names: the provider
  key names OpenVault custodies (:mod:`.ov_key_envs`), the few secrets Cortex
  itself holds, and each one's ``<ENV>_FILE`` form.
* :func:`scrub` is the env a child process gets.
* :func:`read_key` reads a credential per call from env or a mounted
  ``<ENV>_FILE`` (cached at most :data:`FILE_CACHE_S`), and never writes
  ``os.environ``.
* :func:`redact_secrets` takes key shapes and live secret values out of error
  text before it is stored or shown.

Cortex never reads a provider key here to call a model; model calls go
through OpenVault FreeRoute only.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Iterable, Mapping

from CortexOS.integrations.harness.ov_key_envs import OV_KEY_ENVS

FILE_SUFFIX = "_FILE"
FILE_CACHE_S = 5.0
MAX_KEY_FILE_BYTES = 64 * 1024
#: Dev-only: the crew laptop shell keeps provider keys. The isolate path never does.
KEEP_PROVIDER_KEYS_ENV = "CREW_SHELL_KEEP_PROVIDER_KEYS"
#: Secrets Cortex holds that OpenVault's catalogue does not name.
EXTRA_SECRET_ENVS: tuple[str, ...] = (
    "CORTEX_FREEROUTE_TOKEN",
    "XAI_API_KEY",
    "GMAIL_APP_PASSWORD",
)
#: OpenVault lists these as ``custom`` keys, not model providers; ``gh`` needs one.
NOT_SCRUBBED: frozenset[str] = frozenset({"GH_TOKEN", "GITHUB_TOKEN"})
_MIN_VALUE_LEN = 6
REDACTED = "<redacted>"

_KEY_SHAPES = re.compile(
    r"(?i:bearer)\s+\S+"
    r"|ov_[A-Za-z0-9_\-]+"
    r"|sk-[A-Za-z0-9_\-]{8,}"
    r"|(?:gsk|ghp|gho|ghs|ghu|github_pat|hf)_[A-Za-z0-9_]+"
    r"|\b(?:nvapi-|AIza|csk-|xai-)[^\s\"',;)]*"
    r"|[A-Za-z0-9_\-]{32,}"
)


def secret_env_names() -> tuple[str, ...]:
    """Every secret env name, then each one's ``_FILE`` form. No duplicates."""
    base = tuple(
        n for n in dict.fromkeys((*OV_KEY_ENVS, *EXTRA_SECRET_ENVS)) if n not in NOT_SCRUBBED
    )
    return base + tuple(n + FILE_SUFFIX for n in base)


def scrub(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy of ``env`` (default ``os.environ``) without any secret env name."""
    drop = frozenset(secret_env_names())
    src = os.environ if env is None else env
    return {k: v for k, v in src.items() if k not in drop}


def keep_provider_keys(env: Mapping[str, str] | None = None) -> bool:
    src = os.environ if env is None else env
    return (src.get(KEEP_PROVIDER_KEYS_ENV) or "").strip() == "1"


# -- credential reads ------------------------------------------------------------

_file_lock = threading.Lock()
_file_cache: dict[str, tuple[float, str]] = {}


def _now() -> float:
    return time.monotonic()


def _read_file(path: str) -> str:
    now = _now()
    with _file_lock:
        hit = _file_cache.get(path)
        if hit is not None and now - hit[0] < FILE_CACHE_S:
            return hit[1]
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_KEY_FILE_BYTES + 1)
    except OSError:
        raw = b""
    value = "" if len(raw) > MAX_KEY_FILE_BYTES else raw.decode("utf-8", errors="replace").strip()
    with _file_lock:
        _file_cache[path] = (now, value)
    return value


def clear_file_cache() -> None:
    with _file_lock:
        _file_cache.clear()


def read_key(names: Iterable[str], env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """(value, source env name) for the first set name, else ``("", "")``.

    Per name, the env value wins, then ``<NAME>_FILE`` (a mounted secret that
    rotates without a restart). Read per call; ``os.environ`` is never written.
    The source name is safe to show; the value never is.
    """
    src = os.environ if env is None else env
    for name in names:
        value = (src.get(name) or "").strip()
        if value:
            return value, name
        path = (src.get(name + FILE_SUFFIX) or "").strip()
        if path:
            value = _read_file(path)
            if value:
                return value, name + FILE_SUFFIX
    return "", ""


# -- redaction -------------------------------------------------------------------


def _live_values(env: Mapping[str, str]) -> list[str]:
    vals: set[str] = set()
    for name in secret_env_names():
        raw = (env.get(name) or "").strip()
        if not raw:
            continue
        if name.endswith(FILE_SUFFIX):
            raw = _read_file(raw)
        if len(raw) >= _MIN_VALUE_LEN:
            vals.add(raw)
    return sorted(vals, key=len, reverse=True)


def redact_secrets(text: object, *, env: Mapping[str, str] | None = None, limit: int | None = None) -> str:
    """Error text with live secret values and key shapes replaced by :data:`REDACTED`."""
    out = str(text or "")
    for value in _live_values(os.environ if env is None else env):
        out = out.replace(value, REDACTED)
    out = _KEY_SHAPES.sub(REDACTED, out)
    return out if limit is None else out[:limit]


__all__ = [
    "EXTRA_SECRET_ENVS",
    "FILE_CACHE_S",
    "FILE_SUFFIX",
    "KEEP_PROVIDER_KEYS_ENV",
    "NOT_SCRUBBED",
    "REDACTED",
    "clear_file_cache",
    "keep_provider_keys",
    "read_key",
    "redact_secrets",
    "scrub",
    "secret_env_names",
]
