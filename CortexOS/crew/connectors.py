"""Grok Bot plugins -> Netie layers. Stolen catalog shape from rakazo composio-emulator.

Crew does not invent a second OAuth broker. Each row names the Netie owner and
whether it is live on this machine (short HTTP probe, no secrets).
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
)


def _up(url: str, timeout: float = 1.2) -> bool:
    if not url or os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 400
    except Exception:
        return False


def catalog(*, uacc_enabled: bool = False, uacc_armed: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _ROWS:
        connected = False
        if row["slug"] == "uacc":
            connected = bool(uacc_enabled and uacc_armed)
        elif row["slug"] == "github":
            from CortexOS.crew import github as github_mod

            connected = github_mod.available()
        elif row["slug"] == "gmail":
            from CortexOS.crew import inbox as inbox_mod

            connected = inbox_mod.configured()
        elif row["probe"]:
            connected = _up(row["probe"])
        elif row["slug"] == "cursor":
            connected = True
        out.append(
            {
                "slug": row["slug"],
                "name": row["name"],
                "layer": row["layer"],
                "connected": connected,
                "noAuth": True,
                "logo": None,
            }
        )
    return out
