"""CREW-8020-FACTS: facts.md HUD chrome + founder-restart live :8020 prove.

Closes the honest leftover from EPIC-CREW #116 + CREW-SCALE-BUILD #210:
the Memory panel already listed facts, but AppShell Memory did not scroll
to the painted facts.md block, and live :8020 after /scale+/build was only
a STATUS note.

This module:

* inspects the served HUD for Memory list/save/search/export + Build/Scale
* list/save/search/export markdown on a CrewMemory (facts.md survives a
  transcript-only clear, which is asserted by the runtime/HTTP tests)
* GET-probes live :8020 after a founder restart

It does **not** start, restart, or kill :8020 (R-0015). Host-down is
``NOT_PROVEN``, never invent-green COMPLETE / RELEASE / GitHub CI live-host.
Buyer Control+Crew may still be FAR. CoT leftover #212 stays OPEN.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from CortexOS.crew.memory import FACTS_FILE, CrewMemory

SLICE = "CREW-8020-FACTS"
ISSUE = 232
PARENT_EPIC = 116
SCALE_BUILD_PR = 210
SCALE_BUILD_SHA = "c0385603"
DEFAULT_LIVE_URL = "http://127.0.0.1:8020"
LIVE_TIMEOUT_S = 0.8

STATUS_HUD = "HUD"
STATUS_REACHABLE = "REACHABLE"
STATUS_NOT_PROVEN = "NOT_PROVEN"
STATUS_HUD_MISS = "HUD_MISS"

LAW = (
    "Memory on Crew :8020 list/save/search/export markdown via facts.md; "
    "facts survive Clear chat and paint on the HUD Memory panel (R-0001). "
    "Live :8020 after founder restart may be probed GET-only; host-down is "
    "NOT_PROVEN. Never invent CI live-host green, RELEASE, or product COMPLETE. "
    "Do not kill :8020 (R-0015)."
)

# Painted HUD (served index.html). A missing marker is a HUD_MISS, not a pass.
HUD_MARKERS: tuple[str, ...] = (
    'id="memoryPanel"',
    'id="memory"',
    'id="memSave"',
    'id="memExport"',
    'id="memSearch"',
    "facts.md",
    "facts_survived",
    "survives Clear chat",
    "(f.body",
    'data-nav="memory"',
    'focus === "memory"',
    "scrollIntoView",
    'id="ticketsScale"',
    "data-build=",
    "/crew/tickets/build",
    "/crew/tickets/scale",
    "never one agent per issue",
)


def honesty() -> dict[str, Any]:
    """Stamps every CREW-8020-FACTS envelope. Callers cannot opt into COMPLETE."""
    return {
        "slice": SLICE,
        "issue": ISSUE,
        "parent_epic": PARENT_EPIC,
        "scale_build_pr": SCALE_BUILD_PR,
        "scale_build_sha": SCALE_BUILD_SHA,
        "complete": False,
        "live_host_complete": False,
        "live_5000_ci": False,
        "live_8020_ci": False,
        "release": False,
        "issue_212_complete": False,
        "trained_jepa": False,
        "langgraph": False,
        "n8n": False,
        "memgpt": False,
        "kills_8020": False,
        "law": LAW,
    }


def inspect_hud(html: str) -> dict[str, Any]:
    """Assert the painted HUD source, not a screenshot (R-0001)."""
    text = html or ""
    missing = [mark for mark in HUD_MARKERS if mark not in text]
    return {
        "ok": not missing,
        "file": FACTS_FILE,
        "missing": missing,
        "markers": list(HUD_MARKERS),
    }


def prove_facts_store(mem: CrewMemory) -> dict[str, Any]:
    """List/save/search/export markdown on one collection. Does not clear chat."""
    mem.remember("crew-port", "which port crew listens on", "8020")
    listed = mem.list_facts()
    searched = mem.search("port")
    exported = mem.export_markdown()
    facts_path = mem.root / FACTS_FILE
    payload = mem.public_payload()
    ok = (
        bool(listed)
        and listed[0].name == "crew-port"
        and listed[0].body == "8020"
        and bool(searched)
        and searched[0].name == "crew-port"
        and exported.startswith("# Crew facts")
        and "8020" in exported
        and facts_path.is_file()
        and payload.get("file") == FACTS_FILE
    )
    return {
        **honesty(),
        "ok": ok,
        "file": FACTS_FILE,
        "filename": mem.export_filename(),
        "listed": [fact.name for fact in listed],
        "searched": [fact.name for fact in searched],
        "exported": exported,
        "path": str(facts_path),
    }


def public_map(html: str) -> dict[str, Any]:
    """GET /crew/facts-prove. HUD chrome from served index. No live probe."""
    hud = inspect_hud(html)
    return {
        **honesty(),
        "ok": hud["ok"],
        "status": STATUS_HUD if hud["ok"] else STATUS_HUD_MISS,
        "host_up": False,
        "live_probe": "GET /crew/facts-prove/live after founder restart; fail-closed if down",
        "hud": hud,
        "file": FACTS_FILE,
        "execute": "GET /crew/facts-prove/live",
        "mutate": False,
        "founder_restart_required": True,
    }


def live_probes_on() -> bool:
    return os.environ.get("CREW_LIVE_PROBES", "1") != "0"


def live_url() -> str:
    return (
        os.environ.get("CREW_FACTS_PROVE_URL")
        or os.environ.get("CREW_URL")
        or DEFAULT_LIVE_URL
    ).strip().rstrip("/")


def probe_live(
    url: str | None = None,
    *,
    timeout: float = LIVE_TIMEOUT_S,
    get: Any = None,
) -> dict[str, Any]:
    """GET-only founder-restart prove. Never POSTs. Never kills :8020.

    ``host_up`` is a reachability bit for the operator. It is not
    ``live_8020_ci`` and not ``live_host_complete``.
    """
    target = (url or live_url()).strip().rstrip("/") or DEFAULT_LIVE_URL
    base = {
        **honesty(),
        "url": target,
        "mutate": False,
        "kills_8020": False,
        "method": "GET",
    }
    if not live_probes_on():
        return {
            **base,
            "ok": False,
            "status": STATUS_NOT_PROVEN,
            "host_up": False,
            "detail": "CREW_LIVE_PROBES=0; not invent-green",
            "hud": {"ok": False, "missing": list(HUD_MARKERS), "file": FACTS_FILE},
        }

    getter = get or _get
    try:
        code, body, content_type = getter(target + "/", timeout)
    except Exception as exc:  # noqa: BLE001 - probe must never raise into Crew
        return {
            **base,
            "ok": False,
            "status": STATUS_NOT_PROVEN,
            "host_up": False,
            "detail": f"{type(exc).__name__}: host not up (founder restart required)",
            "hud": {"ok": False, "missing": list(HUD_MARKERS), "file": FACTS_FILE},
        }

    host_up = 200 <= int(code) < 500
    if not host_up:
        return {
            **base,
            "ok": False,
            "status": STATUS_NOT_PROVEN,
            "host_up": False,
            "http": code,
            "content_type": content_type,
            "detail": f"HTTP {code}; not invent-green",
            "hud": {"ok": False, "missing": list(HUD_MARKERS), "file": FACTS_FILE},
        }

    hud = inspect_hud(body)
    health = _probe_health(target, timeout, getter)
    status = STATUS_REACHABLE if hud["ok"] else STATUS_HUD_MISS
    return {
        **base,
        "ok": hud["ok"],
        "status": status,
        "host_up": True,
        "http": code,
        "content_type": content_type,
        "detail": (
            "reachable after founder restart; not GitHub CI live-host green"
            if hud["ok"]
            else "host up but HUD chrome missing"
        ),
        "hud": hud,
        "health": health,
        "build_scale": {
            "tickets_scale": 'id="ticketsScale"' in body,
            "build_button": "data-build=" in body,
            "build_post": "/crew/tickets/build" in body,
            "scale_post": "/crew/tickets/scale" in body,
        },
    }


def _probe_health(target: str, timeout: float, getter: Any) -> dict[str, Any]:
    try:
        code, body, _ctype = getter(target + "/crew/health", timeout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": type(exc).__name__}
    return {
        "ok": 200 <= int(code) < 300,
        "http": code,
        "crew_ok": '"ok": true' in body.lower() or '"ok":true' in body.lower(),
    }


def _get(url: str, timeout: float) -> tuple[int, str, str]:
    """stdlib GET. No POST. No redirect to a shutdown URL."""
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(2_000_000)
        ctype = str(resp.headers.get("Content-Type") or "")
        text = raw.decode("utf-8", errors="replace")
        return int(resp.status), text, ctype


def main(argv: list[str] | None = None) -> int:
    """Founder-restart CLI. Exit 2 = NOT_PROVEN (host down). Never kills :8020."""
    args = list(sys.argv[1:] if argv is None else argv)
    url = args[0] if args and not args[0].startswith("-") else live_url()
    report = probe_live(url)
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    status = str(report.get("status") or "")
    if status == STATUS_NOT_PROVEN:
        return 2
    if status == STATUS_HUD_MISS or not report.get("ok"):
        return 1
    return 0


def refuse_invent_green(flags: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Any attempt to stamp live-host COMPLETE is a refusal, not a rewrite."""
    raw = flags if isinstance(flags, Mapping) else {}
    reasons: list[str] = []
    if raw.get("complete") or raw.get("live_host_complete") or raw.get("release"):
        reasons.append("BAN: invent live-host COMPLETE / RELEASE")
    if raw.get("live_8020_ci") or raw.get("live_5000_ci"):
        reasons.append("BAN: invent live-host :5000/:8020 green")
    if raw.get("issue_212_complete") or raw.get("closes_212"):
        reasons.append("BAN: invent COMPLETE on CoT/#212")
    if raw.get("trained_jepa") or raw.get("trained"):
        reasons.append("BAN: invent trained JEPA")
    if raw.get("kill_8020") or raw.get("kills_8020"):
        reasons.append("BAN: do not kill :8020 (R-0015)")
    refused = bool(reasons)
    return {
        **honesty(),
        "ok": not refused,
        "refused": refused,
        "reasons": reasons,
        "status": "REFUSE" if refused else STATUS_HUD,
    }


def hud_path() -> Path:
    return Path(__file__).resolve().parent / "ui" / "index.html"


if __name__ == "__main__":
    raise SystemExit(main())
