"""Cortex operator AppShell catalog. Compose planes; do not collapse them.

Brains=Cortex. Keys=OpenVault. Launch/status=Control. Chat-spawn=Crew.
Canvas=Constructor. Control is GET-display only (F-0030). Never POST
run/goal/route/secrets. Control tiles are deep-links, never Crew spawn.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from CortexOS.execution.rsf_operate import render_audit_operate

BANNER_F0030 = "Display only F-0030"
CONTROL_DEFAULT = "http://127.0.0.1:8040"
DMS_DEFAULT = "http://127.0.0.1:3000"
AIRGPT_DEFAULT = "http://127.0.0.1:8765"
OPENVAULT_DEFAULT = "http://127.0.0.1:5000"
POINTER_DEFAULT = "http://127.0.0.1:8030"
SPACE_DEFAULT = "http://127.0.0.1:3000"

NAV: tuple[dict[str, str], ...] = (
    {"id": "home", "label": "Home", "plane": "home", "focus": "", "hint": "Operator home. Planes stay separate."},
    {"id": "crew", "label": "Crew", "plane": "crew", "focus": "", "hint": "Chat-spawn. Manager converse."},
    {"id": "agents", "label": "Agents", "plane": "crew", "focus": "agents", "hint": "Crew belt teammates. Not Control."},
    {"id": "control", "label": "Control", "plane": "control", "focus": "", "hint": "Control :8040 display GET. F-0030."},
    {"id": "apps", "label": "Apps", "plane": "apps", "focus": "", "hint": "Launcher tiles. Deep-link / iframe / panel."},
    {"id": "audit", "label": "Audit", "plane": "audit", "focus": "", "hint": "Constructor RSF Audit/Operate. No invent-green."},
    {"id": "assign", "label": "Assign", "plane": "crew", "focus": "assign", "hint": "Crew tickets /assign. Control does not assign."},
    {"id": "insights", "label": "Insights", "plane": "insights", "focus": "", "hint": "Intent then ontology where+importance then DMS ask. CERTIFIED|ABSTAIN|REFUSE."},
    {"id": "memory", "label": "Memory", "plane": "crew", "focus": "memory", "hint": "Crew facts.md collections."},
    {"id": "settings", "label": "Settings", "plane": "crew", "focus": "settings", "hint": "Providers. Keys stay in OpenVault."},
)

CONTROL_GET_PATHS: tuple[str, ...] = ("/", "/health", "/v1/belt")
_BANNED_PATH = re.compile(
    r"(^|/)(run|goal|route|secrets|converse|spawn)(/|$)",
    re.I,
)

GetFn = Callable[[str, float], tuple[int, str, str]]


def _env_url(name: str, default: str) -> str:
    return (os.environ.get(name, default) or default).strip().rstrip("/")


def _live_probes() -> bool:
    return os.environ.get("CREW_LIVE_PROBES", "1") != "0"


def control_url() -> str:
    return _env_url("CREW_CONTROL_URL", CONTROL_DEFAULT)


def constructor_url(engine_url: str) -> str:
    base = (engine_url or "http://127.0.0.1:8010").rstrip("/")
    return base + "/cortex/constructor/"


def allowed_control_path(path: str) -> bool:
    norm = "/" + str(path or "").strip().lstrip("/")
    if norm != "/" :
        norm = norm.rstrip("/") or "/"
    if _BANNED_PATH.search(norm):
        return False
    return norm in CONTROL_GET_PATHS


def _get(url: str, timeout: float) -> tuple[int, str, str]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(4000)
        ctype = str(resp.headers.get("Content-Type") or "")
        return int(resp.status), raw.decode("utf-8", "replace"), ctype


def probe(
    url: str,
    *,
    timeout: float = 1.2,
    get: GetFn | None = None,
    live: bool | None = None,
) -> dict[str, Any]:
    if not url:
        return {"ok": False, "status": "unprobed", "detail": "no url", "http": None}
    if not ( _live_probes() if live is None else live):
        return {"ok": False, "status": "unprobed", "detail": "CREW_LIVE_PROBES=0", "http": None}
    getter = get or _get
    try:
        code, _body, _ctype = getter(url, timeout)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {
            "ok": False,
            "status": "down",
            "detail": f"{type(exc).__name__}: {exc}"[:200],
            "http": None,
        }
    ok = 200 <= int(code) < 400
    return {
        "ok": ok,
        "status": "up" if ok else "down",
        "detail": f"HTTP {code}",
        "http": int(code),
    }


def _app(
    *,
    slug: str,
    name: str,
    layer: str,
    url: str,
    probe_url: str,
    spawn: bool = False,
    note: str = "",
) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": name,
        "layer": layer,
        "url": url,
        "probe": probe_url,
        "open": ["deep-link", "iframe", "panel"],
        "spawn": bool(spawn),
        "note": note,
    }


def apps(*, engine_url: str) -> list[dict[str, Any]]:
    ctor = constructor_url(engine_url)
    ov = _env_url("CREW_OPENVAULT_URL", OPENVAULT_DEFAULT)
    dms = _env_url("CREW_DMS_URL", DMS_DEFAULT)
    return [
        _app(
            slug="dms",
            name="DMS",
            layer="consumer",
            url=dms,
            probe_url=dms + "/",
            note="Governed ask consumer. Not the engine.",
        ),
        _app(
            slug="constructor",
            name="Constructor",
            layer="canvas",
            url=ctor,
            probe_url=ctor,
            note="Canvas. RSF Audit/Operate lives here.",
        ),
        _app(
            slug="airgpt",
            name="AirGPT",
            layer="host shell",
            url=_env_url("CREW_AIRGPT_URL", AIRGPT_DEFAULT),
            probe_url=_env_url("CREW_AIRGPT_URL", AIRGPT_DEFAULT) + "/health",
            note="Phone / settings chrome. Not a second vault.",
        ),
        _app(
            slug="openvault",
            name="OpenVault",
            layer="keys",
            url=ov,
            probe_url=ov + "/api/healthz",
            note="Only key vault. Crew does not mint a second vault.",
        ),
        _app(
            slug="pointer",
            name="Pointer",
            layer="act",
            url=_env_url("CREW_POINTER_URL", POINTER_DEFAULT),
            probe_url=_env_url("CREW_POINTER_URL", POINTER_DEFAULT) + "/",
            note="Act / computer-control client. Not Crew spawn.",
        ),
        _app(
            slug="space",
            name="Space",
            layer="spaces",
            url=_env_url("CREW_SPACE_URL", SPACE_DEFAULT),
            probe_url=_env_url("CREW_SPACE_URL", SPACE_DEFAULT) + "/",
            note="DMS Spaces product surface.",
        ),
        _app(
            slug="control",
            name="Control",
            layer="launch / status",
            url=control_url(),
            probe_url=control_url() + "/",
            spawn=False,
            note=BANNER_F0030 + ". Deep-link only. Not Crew spawn.",
        ),
    ]


def palette() -> list[dict[str, str]]:
    rows = [
        {
            "id": item["id"],
            "label": item["label"],
            "hash": "#/" + item["id"],
            "kind": "page",
            "hint": item["hint"],
        }
        for item in NAV
    ]
    rows.append(
        {
            "id": "apps-control",
            "label": "Control display",
            "hash": "#/control",
            "kind": "page",
            "hint": BANNER_F0030,
        }
    )
    return rows


def catalog(*, engine_url: str) -> dict[str, Any]:
    return {
        "ok": True,
        "law": "brains=Cortex keys=OpenVault launch=Control chat-spawn=Crew canvas=Constructor",
        "banner": BANNER_F0030,
        "nav": [dict(item) for item in NAV],
        "apps": apps(engine_url=engine_url),
        "palette": palette(),
        "control": {
            "url": control_url(),
            "display_only": True,
            "banner": BANNER_F0030,
            "get_paths": list(CONTROL_GET_PATHS),
            "spawn": False,
            "converse": False,
        },
        "audit": {
            "constructor_url": constructor_url(engine_url),
            "statuses": ["CERTIFIED", "ABSTAIN", "REFUSE"],
            "invented_certified": False,
        },
    }


def health(
    *,
    engine_url: str,
    get: GetFn | None = None,
    live: bool | None = None,
) -> dict[str, Any]:
    tiles = []
    for app in apps(engine_url=engine_url):
        hit = probe(str(app["probe"]), get=get, live=live)
        tiles.append(
            {
                "slug": app["slug"],
                "name": app["name"],
                "url": app["url"],
                "spawn": app["spawn"],
                **hit,
            }
        )
    return {"ok": True, "live_probes": _live_probes() if live is None else live, "apps": tiles}


def refuse_control_spawn() -> dict[str, Any]:
    return {
        "ok": False,
        "spawn": False,
        "reason": "Control launchers are display deep-links, not Crew spawn",
        "banner": BANNER_F0030,
    }


def control_display(
    *,
    get: GetFn | None = None,
    live: bool | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    base = control_url()
    if path is not None and not allowed_control_path(path):
        return {
            "ok": False,
            "refused": True,
            "display_only": True,
            "banner": BANNER_F0030,
            "reason": "not a Control display GET",
            "path": path,
            "control_url": base,
            "surfaces": [],
        }
    if path:
        norm = "/" + path.strip().lstrip("/")
        if norm != "/":
            norm = norm.rstrip("/") or "/"
        want = [norm]
    else:
        want = list(CONTROL_GET_PATHS)
    surfaces: list[dict[str, Any]] = []
    probing = _live_probes() if live is None else live
    for item in want:
        url = base + ("" if item == "/" else item)
        if not probing:
            surfaces.append(
                {
                    "path": item,
                    "url": url,
                    "ok": False,
                    "status": "unprobed",
                    "detail": "CREW_LIVE_PROBES=0",
                    "body": None,
                }
            )
            continue
        getter = get or _get
        try:
            code, body, ctype = getter(url, 1.2)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            surfaces.append(
                {
                    "path": item,
                    "url": url,
                    "ok": False,
                    "status": "down",
                    "detail": f"{type(exc).__name__}: {exc}"[:200],
                    "body": None,
                }
            )
            continue
        snippet = body[:1500]
        parsed: Any = None
        if "json" in (ctype or "").lower():
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None
        surfaces.append(
            {
                "path": item,
                "url": url,
                "ok": 200 <= int(code) < 400,
                "status": "up" if 200 <= int(code) < 400 else "down",
                "detail": f"HTTP {code}",
                "http": int(code),
                "content_type": ctype,
                "body": parsed if parsed is not None else snippet,
            }
        )
    any_up = any(row.get("ok") for row in surfaces)
    return {
        "ok": any_up,
        "display_only": True,
        "banner": BANNER_F0030,
        "converse": False,
        "spawn": False,
        "control_url": base,
        "surfaces": surfaces,
    }


def audit_payload(run: Mapping[str, Any] | None = None) -> dict[str, Any]:
    views = render_audit_operate(run)
    audit = views["audit"]
    operate = views["operate"]
    return {
        "ok": bool(audit.get("ok")),
        "constructor_url": constructor_url(_env_url("CREW_ENGINE_URL", "http://127.0.0.1:8010")),
        "invented_certified": False,
        "statuses": ["CERTIFIED", "ABSTAIN", "REFUSE"],
        "audit": audit,
        "operate": operate,
        "source": "rsf_operate",
    }


__all__ = [
    "BANNER_F0030",
    "CONTROL_GET_PATHS",
    "NAV",
    "allowed_control_path",
    "apps",
    "audit_payload",
    "catalog",
    "control_display",
    "control_url",
    "health",
    "palette",
    "probe",
    "refuse_control_spawn",
]
