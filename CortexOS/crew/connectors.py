"""Grok Bot plugins -> Netie layers. Stolen catalog shape from rakazo composio-emulator.

Crew does not invent a second OAuth broker. Each row names the Netie owner and
whether it is live on this machine (short HTTP probe, no secrets). API hosts
are first-class rows so the operator can see which inference connector is
actually up. A selected connector that is down refuses with a reason; callers
must not walk to another host (KB R-0011).
"""

from __future__ import annotations

import os
import urllib.request
from typing import Any

# Rakazo emulator slugs plus GROK_SYNC inherit map.
_ROWS: tuple[dict[str, str], ...] = (
    {"slug": "openvault", "name": "OpenVault", "layer": "keys / FreeRoute", "probe": "http://127.0.0.1:5000/api/healthz"},
    {"slug": "cortex", "name": "Cortex engine", "layer": "governed ask", "probe": "http://127.0.0.1:8010/api/engine/activity"},
    {"slug": "plane", "name": "Plane board", "layer": "tickets / holds", "probe": "http://127.0.0.1:8099/netie/"},
    {"slug": "uacc", "name": "UACC laptop", "layer": "mouse/keyboard, human confirm", "probe": ""},
    {"slug": "gmail", "name": "Gmail", "layer": "IMAP headers or drop .eml; Crew never sends", "probe": ""},
    {"slug": "github", "name": "GitHub", "layer": "gh pr list in chat; no auto-merge", "probe": ""},
    {"slug": "slack", "name": "Slack", "layer": "not a second board", "probe": ""},
    {"slug": "notion", "name": "Notion", "layer": "operator paste", "probe": ""},
    {"slug": "cursor", "name": "Cursor", "layer": "this IDE; no infinite cloud swarm", "probe": ""},
    {"slug": "grok", "name": "Grok Bot", "layer": "OFFLOADED to Crew :8020; watchdog must not auto-start", "probe": ""},
    {"slug": "mcp", "name": "MCP", "layer": "lazy tools; arm per server", "probe": ""},
)

# Inference APIs the router can pin. Presence of the env key is the probe;
# secrets never leave this process in the catalog payload.
_API_ROWS: tuple[dict[str, str], ...] = (
    {"slug": "anthropic", "name": "Anthropic", "layer": "API / inference", "env": "ANTHROPIC_API_KEY"},
    {"slug": "openrouter", "name": "OpenRouter", "layer": "API / inference", "env": "OPENROUTER_API_KEY"},
    {"slug": "openai", "name": "OpenAI / compatible", "layer": "API / inference", "env": "OPENAI_API_KEY"},
    {"slug": "deepseek", "name": "DeepSeek", "layer": "API / inference", "env": "DEEPSEEK_API_KEY"},
    {"slug": "xai", "name": "xAI / Grok", "layer": "API / inference", "env": "XAI_API_KEY"},
    {"slug": "groq", "name": "Groq", "layer": "API / inference", "env": "GROQ_API_KEY"},
    {"slug": "google", "name": "Google AI", "layer": "API / inference", "env": "GOOGLE_API_KEY"},
    {"slug": "cerebras", "name": "Cerebras", "layer": "API / inference", "env": "CEREBRAS_API_KEY"},
    {"slug": "mistral", "name": "Mistral", "layer": "API / inference", "env": "MISTRAL_API_KEY"},
)


class ConnectorError(RuntimeError):
    """Selected connector is down or unset. Do not silently pick another."""


def _probe(url: str, timeout: float = 1.2) -> tuple[bool, str]:
    if not url:
        return False, "no probe url"
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return False, "CREW_LIVE_PROBES=0"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = int(resp.status)
            if 200 <= code < 400:
                return True, f"HTTP {code}"
            return False, f"HTTP {code}"
    except Exception as exc:  # noqa: BLE001 - probe must never raise into a turn
        return False, f"{type(exc).__name__}: {exc}"[:200]


def _up(url: str, timeout: float = 1.2) -> bool:
    ok, _detail = _probe(url, timeout=timeout)
    return ok


def _row(
    *,
    slug: str,
    name: str,
    layer: str,
    connected: bool,
    detail: str,
) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": name,
        "layer": layer,
        "connected": connected,
        "detail": detail,
        "ok": connected,
        "noAuth": True,
        "logo": None,
    }


def catalog(*, uacc_enabled: bool = False, uacc_armed: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _ROWS:
        connected = False
        detail = ""
        if row["slug"] == "uacc":
            connected = bool(uacc_enabled and uacc_armed)
            detail = "UACC armed" if connected else "UACC not armed"
        elif row["slug"] == "github":
            from CortexOS.crew import github as github_mod

            if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
                connected, detail = False, "CREW_LIVE_PROBES=0"
            else:
                connected = github_mod.available()
                detail = "gh auth ok" if connected else "gh auth failed or missing"
        elif row["slug"] == "gmail":
            from CortexOS.crew import inbox as inbox_mod

            connected = inbox_mod.configured()
            detail = "IMAP configured" if connected else "IMAP unset; drop .eml"
        elif row["slug"] == "mcp":
            connected = bool(uacc_enabled and uacc_armed)
            detail = "MCP master+UACC armed" if connected else "MCP not armed"
        elif row["probe"]:
            connected, detail = _probe(row["probe"])
        elif row["slug"] == "cursor":
            connected = True
            detail = "this IDE"
        elif row["slug"] == "grok":
            connected = False
            detail = "OFFLOADED; watchdog must not auto-start"
        else:
            connected = False
            detail = "mapped; no live probe"
        out.append(
            _row(
                slug=row["slug"],
                name=row["name"],
                layer=row["layer"],
                connected=connected,
                detail=detail,
            )
        )
    for row in _API_ROWS:
        env_key = row["env"]
        connected = bool(os.environ.get(env_key, "").strip())
        if row["slug"] == "openai" and not connected:
            connected = bool(os.environ.get("CREW_OPENAI_BASE_URL", "").strip())
        detail = f"{env_key} configured" if connected else f"{env_key} unset"
        out.append(
            _row(
                slug=row["slug"],
                name=row["name"],
                layer=row["layer"],
                connected=connected,
                detail=detail,
            )
        )
    return out


def get(slug: str, **catalog_kwargs: Any) -> dict[str, Any] | None:
    needle = (slug or "").strip().lower()
    if needle == "openai-compatible":
        needle = "openai"
    for row in catalog(**catalog_kwargs):
        if str(row.get("slug") or "") == needle:
            return row
    return None


def require(slug: str, **catalog_kwargs: Any) -> dict[str, Any]:
    """Return the connector row or refuse with a reason. No silent fallback."""
    row = get(slug, **catalog_kwargs)
    if row is None:
        raise ConnectorError(f"unknown connector '{slug}' (no silent fallback)")
    if not row.get("connected"):
        reason = str(row.get("detail") or "not connected")
        raise ConnectorError(f"connector {slug} refused: {reason} (no silent fallback)")
    return row
