"""Customer-envelope checks for live asks. Never import DMS.

``cortex_contract.Answer`` is the engine wire (answer/route/provenance). A live
``POST /v1/chat/ask`` returns a DMS envelope (badge/abstained/text/values). The
shapes do not validate as each other, so the live path uses this minimal check
instead of pinning D:\\DMS or ``dms_executor``.
"""
from __future__ import annotations

import os
from typing import Any

ASK_URL_REQUIRED = (
    "DMS_ASK_URL is unset; --live has nowhere to send asks. "
    "Set DMS_ASK_URL to the DMS origin or the full /v1/chat/ask URL. "
    "There is no default host or port."
)

_ABSTAIN_BADGES = frozenset(
    {"ABSTAIN", "abstain", "REFUSED", "refused", "blocked", "BLOCKED"}
)
_REQUIRED = ("badge", "abstained", "audit_id")


def resolve_ask_url(cli_url: str | None = None) -> str | None:
    raw = (cli_url or os.environ.get("DMS_ASK_URL") or "").strip()
    return raw or None


def ask_endpoint(base: str) -> str:
    url = base.rstrip("/")
    if url.endswith("/v1/chat/ask"):
        return url
    return url + "/v1/chat/ask"


def assert_ask_envelope(env: Any) -> None:
    """E1-style checks on the artifact the customer receives. No DMS import."""
    if not isinstance(env, dict):
        raise AssertionError("ask envelope must be an object")
    missing = [k for k in _REQUIRED if k not in env]
    if missing:
        raise AssertionError(f"ask envelope missing {missing}")
    abstained = env.get("abstained")
    if not isinstance(abstained, bool):
        raise AssertionError(f"abstained must be bool, got {type(abstained).__name__}")
    badge = str(env.get("badge") or "")
    if abstained != (badge in _ABSTAIN_BADGES):
        raise AssertionError(f"E1: abstained={abstained} does not match badge={badge!r}")
    if "values" not in env and "rows" not in env:
        raise AssertionError("ask envelope needs values or rows")
    for key in ("sources", "drillthrough_token"):
        if key not in env:
            raise AssertionError(f"ask envelope missing {key!r}")
