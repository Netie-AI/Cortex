"""APPSHELL-BUYER-REACH: documented Netie host path for Control+Crew chrome.

Constructor already lives on the existing public host as
``https://app.netie.ai/cortex`` (not a new hostname). AppShell reuses that
host: ``https://app.netie.ai/appshell`` and the engine-local path
``http://127.0.0.1:8010/appshell``. Crew ``:8020`` remains a tunnel leftover
from #232 / #195 -- NOT buyer COMPLETE.

This module:

* documents the public host + engine path so a private tunnel is not the
  only path
* serves prove notes (founder/platform OK)
* asks OpenVault leave-machine before advertising a non-loopback bind or
  claiming a live public host
* fail-closes when the gate is unarmed, unreachable, or denied

It does **not** start, restart, or kill ``:8020`` / ``:5000`` (R-0015).
It does **not** invent GitHub CI live-host green, RELEASE, or product
COMPLETE. Control stays GET F-0030. CoT leftover #212 stays OPEN.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from CortexOS.integrations.openvault_client import is_loopback_url

SLICE = "APPSHELL-BUYER-REACH"
ISSUE = 237
PARENT_EPIC = 236
APPSHELL_PR = 195
APPSHELL_SHA = "a0f1723b"
FACTS_ISSUE = 232
FACTS_SHA = "adcb0369"

PUBLIC_ORIGIN = "https://app.netie.ai"
PUBLIC_PATH = "/appshell"
PUBLIC_URL = PUBLIC_ORIGIN + PUBLIC_PATH
ENGINE_LOCAL_ORIGIN = "http://127.0.0.1:8010"
ENGINE_LOCAL_URL = ENGINE_LOCAL_ORIGIN + PUBLIC_PATH
TUNNEL_LEFTOVER = "http://127.0.0.1:8020"
LEAVE_ACTION = "leave"
LEAVE_DESTINATION = "appshell-host"
LIVE_TIMEOUT_S = 0.8

STATUS_DOCUMENTED = "DOCUMENTED"
STATUS_TUNNEL_LEFTOVER = "TUNNEL_LEFTOVER"
STATUS_HOST_DENIED = "HOST_DENIED"
STATUS_NOT_PROVEN = "NOT_PROVEN"
STATUS_HOST_REACHABLE = "HOST_REACHABLE"
STATUS_HUD_MISS = "HUD_MISS"
STATUS_REFUSE = "REFUSE"

LAW = (
    "Control+Crew AppShell buyer path is the existing Netie host "
    "https://app.netie.ai/appshell (same host as /cortex, not a new name) "
    "or the engine path GET /appshell. Tunnel :8020 leftover is NOT buyer "
    "COMPLETE. OpenVault leave-machine gates non-loopback bind / live public "
    "claim; unarmed, unreachable, or denied fail-closed. Never invent CI "
    "live-host green, RELEASE, or product COMPLETE. Control stays GET "
    "F-0030 -- never POST run/goal/route/secrets. Do not kill :8020/:5000 "
    "(R-0015). #212 stays OPEN."
)

HUD_MARKERS: tuple[str, ...] = (
    'id="appshell"',
    'aria-label="AppShell"',
    'data-nav="control"',
    'data-nav="crew"',
    'data-host-path="/appshell"',
    'data-public-host="https://app.netie.ai/appshell"',
    "Display only F-0030",
    'id="plane-control"',
    'id="controlFrame"',
    'id="appTiles"',
    'id="plane-home"',
)


def honesty() -> dict[str, Any]:
    """Stamps every APPSHELL-BUYER-REACH envelope. Callers cannot opt into COMPLETE."""
    return {
        "slice": SLICE,
        "issue": ISSUE,
        "parent_epic": PARENT_EPIC,
        "appshell_pr": APPSHELL_PR,
        "appshell_sha": APPSHELL_SHA,
        "facts_issue": FACTS_ISSUE,
        "facts_sha": FACTS_SHA,
        "complete": False,
        "buyer_complete": False,
        "live_host_complete": False,
        "live_5000_ci": False,
        "live_8020_ci": False,
        "release": False,
        "issue_212_complete": False,
        "trained_jepa": False,
        "langgraph": False,
        "n8n": False,
        "second_vault": False,
        "kills_8020": False,
        "kills_5000": False,
        "control_post": False,
        "law": LAW,
    }


def public_origin() -> str:
    return (os.environ.get("CORTEX_PUBLIC_ORIGIN") or PUBLIC_ORIGIN).strip().rstrip("/")


def public_url() -> str:
    origin = public_origin()
    return origin + PUBLIC_PATH if origin else PUBLIC_URL


def advertised_url() -> str:
    override = (os.environ.get("CREW_APPSHELL_HOST_URL") or "").strip().rstrip("/")
    if override:
        return override
    return public_url()


def inspect_hud(html: str) -> dict[str, Any]:
    """Assert the painted AppShell chrome, not a screenshot (R-0001)."""
    text = html or ""
    missing = [mark for mark in HUD_MARKERS if mark not in text]
    return {
        "ok": not missing,
        "missing": missing,
        "markers": list(HUD_MARKERS),
        "host_path": PUBLIC_PATH,
        "public_url": PUBLIC_URL,
    }


def catalog_stamp() -> dict[str, Any]:
    """Documented host map. Tunnel leftover stays visible and not COMPLETE."""
    return {
        **honesty(),
        "ok": True,
        "status": STATUS_DOCUMENTED,
        "host_path": PUBLIC_PATH,
        "public_url": public_url(),
        "public_origin": public_origin(),
        "engine_local_url": ENGINE_LOCAL_URL,
        "tunnel_leftover": TUNNEL_LEFTOVER,
        "tunnel_only": False,
        "tunnel_complete": False,
        "new_hostname": False,
        "same_host_as_constructor": True,
        "constructor_public": PUBLIC_ORIGIN + "/cortex",
        "control_display_only": True,
        "banner": "Display only F-0030",
        "leave_action": LEAVE_ACTION,
        "leave_destination": LEAVE_DESTINATION,
        "live_probe": (
            "GET /crew/appshell/host/live after founder/platform prove; "
            "leave-machine fail-closed if unarmed/denied; NOT_PROVEN if down"
        ),
        "execute": "GET /crew/appshell/host/live",
        "mutate": False,
    }


def public_map(html: str) -> dict[str, Any]:
    """GET /crew/appshell/host. HUD chrome from served index. No live probe."""
    hud = inspect_hud(html)
    return {
        **catalog_stamp(),
        "ok": hud["ok"],
        "status": STATUS_DOCUMENTED if hud["ok"] else STATUS_HUD_MISS,
        "host_up": False,
        "hud": hud,
        "founder_prove_required": True,
        "ci_live_host_green": False,
    }


def live_probes_on() -> bool:
    return os.environ.get("CREW_LIVE_PROBES", "1") != "0"


def _is_unspecified_host(host: str) -> bool:
    needle = (host or "").strip().lower()
    return needle in {"0.0.0.0", "::", "[::]"}


def bind_leaves_machine(host: str) -> bool:
    """True when this process would listen beyond loopback."""
    raw = (host or "").strip() or "127.0.0.1"
    if _is_unspecified_host(raw):
        return True
    return not is_loopback_url("http://" + raw.strip("[]") + "/")


def leave_machine(
    *,
    check: Any = None,
    destination: str = LEAVE_DESTINATION,
) -> dict[str, Any]:
    """Ask OpenVault leave-machine. Unreachable / denied fail closed.

    Reuses ``openvault_gate.check_gate`` (same SoT as FreeRoute / RSF).
    Does not mint a second vault.
    """
    dest = (destination or "").strip() or LEAVE_DESTINATION
    try:
        if check is not None:
            gate = check(
                action=LEAVE_ACTION,
                destination=dest,
                required_providers=[],
            )
        else:
            from CortexOS.integrations import openvault_gate

            gate = openvault_gate.check_gate(
                action=LEAVE_ACTION,
                destination=dest,
                required_providers=[],
            )
    except Exception as exc:  # noqa: BLE001 - a broken gate client is a refusal
        return {
            "ok": False,
            "allowed": False,
            "action": LEAVE_ACTION,
            "destination": dest,
            "reasons": [f"OpenVault leave-machine gate error: {type(exc).__name__}: {exc}"[:240]],
            "keys_ready": False,
        }
    raw = gate if isinstance(gate, Mapping) else {}
    allowed = raw.get("allowed") is True
    reasons = list(raw.get("reasons") or [])
    if not allowed and not reasons:
        reasons = ["leave-machine gate denied"]
    return {
        "ok": bool(raw.get("ok", True)) and allowed,
        "allowed": allowed,
        "action": LEAVE_ACTION,
        "destination": dest,
        "reasons": reasons,
        "openvault_url": raw.get("openvault_url"),
        "keys_ready": bool(raw.get("keys_ready")),
    }


def decide_bind(
    host: str,
    *,
    check: Any = None,
) -> dict[str, Any]:
    """May this process bind ``host``? Loopback is leftover; public needs OV."""
    raw_host = (host or "").strip() or "127.0.0.1"
    leaves = bind_leaves_machine(raw_host)
    base = {
        **honesty(),
        "bind_host": raw_host,
        "leaves_machine": leaves,
        "mutate": False,
    }
    if not leaves:
        return {
            **base,
            "ok": True,
            "allowed": True,
            "status": STATUS_TUNNEL_LEFTOVER,
            "detail": (
                "loopback bind is the :8020 leftover path; not buyer COMPLETE; "
                "engine GET /appshell is the documented Netie host path"
            ),
            "gate": {"allowed": True, "skipped": True, "action": LEAVE_ACTION},
        }
    gate = leave_machine(check=check)
    if gate.get("allowed") is True:
        return {
            **base,
            "ok": True,
            "allowed": True,
            "status": STATUS_DOCUMENTED,
            "detail": (
                "OpenVault leave-machine allowed public bind; not GitHub CI "
                "live-host green; not product COMPLETE"
            ),
            "gate": gate,
        }
    reasons = gate.get("reasons") or ["leave-machine gate denied"]
    return {
        **base,
        "ok": False,
        "allowed": False,
        "status": STATUS_HOST_DENIED,
        "detail": "; ".join(str(r) for r in reasons)[:240],
        "gate": gate,
    }


def refuse_invent_green(flags: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Any attempt to stamp live-host COMPLETE is a refusal, not a rewrite."""
    raw = flags if isinstance(flags, Mapping) else {}
    reasons: list[str] = []
    if raw.get("complete") or raw.get("buyer_complete") or raw.get("live_host_complete") or raw.get("release"):
        reasons.append("BAN: invent live-host COMPLETE / RELEASE / buyer COMPLETE")
    if raw.get("live_8020_ci") or raw.get("live_5000_ci") or raw.get("ci_live_host_green"):
        reasons.append("BAN: invent live-host :5000/:8020 green")
    if raw.get("tunnel_only_complete") or raw.get("tunnel_complete"):
        reasons.append("BAN: treat tunnel-only as buyer COMPLETE")
    if raw.get("issue_212_complete") or raw.get("closes_212"):
        reasons.append("BAN: invent COMPLETE on CoT/#212")
    if raw.get("trained_jepa") or raw.get("trained"):
        reasons.append("BAN: invent trained JEPA")
    if raw.get("second_vault") or raw.get("dual_own_openvault"):
        reasons.append("BAN: second vault / dual-own OpenVault SoT")
    if raw.get("kill_8020") or raw.get("kills_8020") or raw.get("kill_5000"):
        reasons.append("BAN: do not kill :8020/:5000 (R-0015)")
    refused = bool(reasons)
    return {
        **honesty(),
        "ok": not refused,
        "refused": refused,
        "reasons": reasons,
        "status": STATUS_REFUSE if refused else STATUS_DOCUMENTED,
    }


def prove_live(
    url: str | None = None,
    *,
    timeout: float = LIVE_TIMEOUT_S,
    get: Any = None,
    check: Any = None,
    bind_host: str | None = None,
) -> dict[str, Any]:
    """GET-only founder/platform prove. Never POSTs Control. Never kills ports.

    Leave-machine is required when the advertised URL is not loopback.
    Host-down / probes-off / denied is ``NOT_PROVEN`` or ``HOST_DENIED``,
    never invent-green COMPLETE / GitHub CI live-host.
    """
    target = (url or advertised_url()).strip().rstrip("/") or PUBLIC_URL
    leaves = not is_loopback_url(target) or _is_unspecified_host(
        urllib.parse.urlsplit(target).hostname or ""
    )
    base = {
        **honesty(),
        "url": target,
        "host_path": PUBLIC_PATH,
        "public_url": public_url(),
        "tunnel_leftover": TUNNEL_LEFTOVER,
        "mutate": False,
        "method": "GET",
        "leaves_machine": leaves,
        "bind_host": bind_host,
    }
    if not live_probes_on():
        return {
            **base,
            "ok": False,
            "status": STATUS_NOT_PROVEN,
            "host_up": False,
            "detail": "CREW_LIVE_PROBES=0; not invent-green",
            "hud": {"ok": False, "missing": list(HUD_MARKERS), "host_path": PUBLIC_PATH},
        }

    if leaves:
        gate = leave_machine(check=check)
        base["gate"] = gate
        if gate.get("allowed") is not True:
            return {
                **base,
                "ok": False,
                "status": STATUS_HOST_DENIED,
                "host_up": False,
                "detail": (
                    "; ".join(str(r) for r in (gate.get("reasons") or ["leave-machine gate denied"]))
                    + "; not invent-green public host"
                )[:240],
                "hud": {"ok": False, "missing": list(HUD_MARKERS), "host_path": PUBLIC_PATH},
            }
    else:
        base["gate"] = {"allowed": True, "skipped": True, "action": LEAVE_ACTION}
        base["status_note"] = STATUS_TUNNEL_LEFTOVER

    getter = get or _get
    try:
        page = target if target.endswith(PUBLIC_PATH) else target + PUBLIC_PATH
        code, body, content_type = getter(page, timeout)
    except Exception as exc:  # noqa: BLE001 - prove must never raise into Crew
        return {
            **base,
            "ok": False,
            "status": STATUS_NOT_PROVEN,
            "host_up": False,
            "detail": f"{type(exc).__name__}: host not up (founder/platform prove required)",
            "hud": {"ok": False, "missing": list(HUD_MARKERS), "host_path": PUBLIC_PATH},
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
            "hud": {"ok": False, "missing": list(HUD_MARKERS), "host_path": PUBLIC_PATH},
        }

    hud = inspect_hud(body)
    status = STATUS_HOST_REACHABLE if hud["ok"] else STATUS_HUD_MISS
    return {
        **base,
        "ok": hud["ok"],
        "status": status,
        "host_up": True,
        "http": code,
        "content_type": content_type,
        "detail": (
            "reachable founder/platform prove; not GitHub CI live-host green"
            if hud["ok"]
            else "host up but AppShell chrome missing"
        ),
        "hud": hud,
        "ci_live_host_green": False,
        "buyer_complete": False,
    }


def _get(url: str, timeout: float) -> tuple[int, str, str]:
    """stdlib GET. No POST. No redirect-to-shutdown."""
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(2_000_000)
        ctype = str(resp.headers.get("Content-Type") or "")
        text = raw.decode("utf-8", errors="replace")
        return int(resp.status), text, ctype


def hud_path() -> Path:
    return Path(__file__).resolve().parent / "ui" / "index.html"


def main(argv: list[str] | None = None) -> int:
    """Founder/platform CLI. Exit 2 = NOT_PROVEN / HOST_DENIED. Never kills ports."""
    args = list(sys.argv[1:] if argv is None else argv)
    url = args[0] if args and not args[0].startswith("-") else advertised_url()
    report = prove_live(url)
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    status = str(report.get("status") or "")
    if status in {STATUS_NOT_PROVEN, STATUS_HOST_DENIED}:
        return 2
    if status == STATUS_HUD_MISS or not report.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
