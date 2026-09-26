"""Secret hygiene (PRD EPIC-HARNESS-ENT R1.4, R1.6, R5.4). Stdlib only at import.

* :func:`secret_env_names` is the one list of secret env names: every provider
  key env in the registry, its ``<ENV>_FILE`` form, and the non-provider
  secrets Cortex holds. ``direct_providers.KEY_ENVS`` is this list, so
  ``freeroute.child_env`` (and ``mcp_client`` / ``app_runner`` through it)
  strips every one of them.
* :func:`scrub` is the env a child process gets.
* :func:`read_key` reads a key per call from env or a mounted ``<ENV>_FILE``
  (cached at most :data:`FILE_CACHE_S`), and never writes ``os.environ``.
* :func:`redact_secrets` takes key shapes, live secret values and PII out of
  error text before it is stored or shown.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Iterable, Mapping

from CortexOS.integrations.harness import registry

FILE_SUFFIX = "_FILE"
FILE_CACHE_S = 5.0
MAX_KEY_FILE_BYTES = 64 * 1024
#: Dev-only: the crew laptop shell keeps provider keys. The enterprise profile refuses it (HX-09).
KEEP_PROVIDER_KEYS_ENV = "CREW_SHELL_KEEP_PROVIDER_KEYS"
#: Secrets Cortex holds that are not provider key rows.
EXTRA_SECRET_ENVS: tuple[str, ...] = (
    "CORTEX_FREEROUTE_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "GMAIL_APP_PASSWORD",
)
_MIN_VALUE_LEN = 6
REDACTED = "<redacted>"

_KEY_SHAPES = re.compile(
    r"(?i:bearer)\s+\S+"
    r"|ov_[A-Za-z0-9_\-]+"
    r"|sk-[A-Za-z0-9_\-]{8,}"
    r"|(?:gsk|ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]+"
    r"|\b(?:nvapi-|AIza|csk-|xai-)[^\s\"',;)]*"
    r"|\bAKIA[0-9A-Z]{12,}"
    r"|[A-Za-z0-9_\-]{32,}"
)


def secret_env_names() -> tuple[str, ...]:
    """Every secret env name, then each one's ``_FILE`` form. No duplicates."""
    base = tuple(dict.fromkeys((*registry.all_key_envs(), *EXTRA_SECRET_ENVS)))
    return base + tuple(n + FILE_SUFFIX for n in base)


def scrub(env: Mapping[str, str] | None = None, *, keep: Iterable[str] = ()) -> dict[str, str]:
    """Copy of ``env`` (default ``os.environ``) without any secret env name.

    ``keep`` names a secret the child legitimately needs; nothing else survives.
    ``GH_TOKEN`` is not a provider secret, so ``gh`` keeps it without ``keep``.
    """
    kept = frozenset(keep)
    drop = frozenset(secret_env_names()) - kept
    src = os.environ if env is None else env
    return {k: v for k, v in src.items() if k not in drop}


def keep_provider_keys(env: Mapping[str, str] | None = None) -> bool:
    src = os.environ if env is None else env
    return (src.get(KEEP_PROVIDER_KEYS_ENV) or "").strip() == "1"


# -- key reads -------------------------------------------------------------------

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


def _mask_pii(text: str) -> str:
    # Lazy: pii_mask pulls CortexOS.security, and this module must import stdlib only.
    from CortexOS.integrations import pii_mask

    masked = pii_mask.mask_messages([{"role": "user", "content": text}])
    return str(masked.messages[0].get("content") or "")


def redact_secrets(text: object, *, env: Mapping[str, str] | None = None, limit: int | None = None) -> str:
    """Error text with key shapes, live secret values and PII removed.

    Fails closed: if PII masking errors, the whole text is withheld.
    """
    out = str(text or "")
    for value in _live_values(os.environ if env is None else env):
        out = out.replace(value, REDACTED)
    out = _KEY_SHAPES.sub(REDACTED, out)
    try:
        out = _mask_pii(out)
    except Exception:  # noqa: BLE001 - any masking failure withholds the text
        return REDACTED
    return out if limit is None else out[:limit]


__all__ = [
    "EXTRA_SECRET_ENVS",
    "FILE_CACHE_S",
    "FILE_SUFFIX",
    "KEEP_PROVIDER_KEYS_ENV",
    "REDACTED",
    "clear_file_cache",
    "keep_provider_keys",
    "read_key",
    "redact_secrets",
    "scrub",
    "secret_env_names",
]
