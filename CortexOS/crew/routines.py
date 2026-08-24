"""Standing Netie routines. Not a second ticket-implementing cron (GROK_SYNC)."""

from __future__ import annotations

from typing import Any


def catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": "NetieEstate24x7",
            "when": "every 15 min",
            "layer": "schtasks + estate-watchdog.ps1",
            "active": True,
            "instruction": "Keep Plane + Crew/OpenVault alive. Do not auto-start Grok Bot. Do not implement tickets here.",
        },
        {
            "name": "Crew night_watch",
            "when": "until 09:00 local when the Cursor loop is armed",
            "layer": "scripts/night_watch.ps1",
            "active": True,
            "instruction": "Restart Crew/OpenVault if down. Log open PRs. Do not write ANS or kind-euclid.",
        },
        {
            "name": "PR check",
            "when": "every night_watch tick + whenever chat asks",
            "layer": "gh pr list via desk_status",
            "active": True,
            "instruction": "Report open PRs in chat or NIGHT_WAKE.md. Do not merge. Do not spawn one cloud agent per PR.",
        },
        {
            "name": "Plane board",
            "when": "live",
            "layer": "http://localhost:8099/netie/",
            "active": True,
            "instruction": "Holds view. Ticket Runner seats existing writers.",
        },
        {
            "name": "Money / Decision",
            "when": "on ask",
            "layer": "human operator",
            "active": True,
            "instruction": "Human is money and decision authority. No auto-pay, auto-send, auto-merge.",
        },
    ]
