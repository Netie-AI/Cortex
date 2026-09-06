"""Assign-task router. Destinations via Cortex -> OpenVault only.

Control GET-displays the coordinate map. POST lives on Crew adapters.
Credentials come from the existing OpenVault-armed Crew router/keys path.
Unarmed destinations refuse. No second vault. HUMAN_STOP for value-moving
(Cursor cloud spawn, live SSH). Preview destinations declare a handoff; they
do not invent a live session.
"""

from __future__ import annotations

import os
import urllib.request
from typing import Any

from CortexOS.crew.llm import chosen_public

AIRGPT_PORT = 8765
AIRGPT_DEFAULT = f"http://127.0.0.1:{AIRGPT_PORT}"

LAW = (
    "Assign executes only through Crew/Cortex adapters. "
    "Control GET-displays the coordinate map. Control does not POST assign. "
    "Credentials via OpenVault-armed Crew router only. No second vault. "
    "HUMAN_STOP for value-moving (cloud spawn, live SSH). Unarmed refuses."
)

HUMAN_STOP = "HUMAN_STOP"

# Display-only map. `status` is honest: live vs preview. Never claimed complete.
_DESTINATIONS: tuple[dict[str, str], ...] = (
    {
        "id": "crew",
        "title": "Cortex Crew",
        "status": "live",
        "adapter": "in-app spawn/brief",
        "needs": "inference route (OpenVault or CREW_MODEL)",
        "honesty": "Live: operator_spawn + A2A brief. Same path as /spawn.",
    },
    {
        "id": "claude_code",
        "title": "Claude Code",
        "status": "preview",
        "adapter": "SSH/CLI laptop lane (declared, not opened)",
        "needs": "anthropic via OpenVault-armed router",
        "honesty": "Preview: declares lane. Does not invent live SSH.",
    },
    {
        "id": "claude_app",
        "title": "Claude App",
        "status": "preview",
        "adapter": "link/handoff",
        "needs": "anthropic via OpenVault-armed router",
        "honesty": "Preview: returns a Claude.ai handoff. Does not call the App API.",
    },
    {
        "id": "cursor_cloud",
        "title": "Cursor Cloud Agent",
        "status": "preview",
        "adapter": "handoff shape only",
        "needs": "cursor via OpenVault-armed router",
        "honesty": "Preview: declares handoff. Does not spawn a cloud agent.",
    },
    {
        "id": "local_model",
        "title": "Local model",
        "status": "live",
        "adapter": "Ollama / loopback OpenAI-compatible",
        "needs": "ollama or vault-armed local base",
        "honesty": "Live when a local host is configured. Unarmed refuses.",
    },
    {
        "id": "airgpt",
        "title": "AirGPT",
        "status": "preview",
        "adapter": f"thin client :{AIRGPT_PORT}",
        "needs": f"AirGPT listening on {AIRGPT_DEFAULT}",
        "honesty": "Preview: probes :8765. Refuses if down. No Crew-held AirGPT key.",
    },
)

_ALIASES = {
    "cortex": "crew",
    "cortex_crew": "crew",
    "claude-code": "claude_code",
    "claude-app": "claude_app",
    "claude": "claude_app",
    "cursor-cloud": "cursor_cloud",
    "cursor_cloud_agent": "cursor_cloud",
    "local": "local_model",
    "ollama": "local_model",
}


class AssignError(RuntimeError):
    """Fail-closed assign. Callers must not walk to another destination."""

    def __init__(self, detail: str, *, code: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code


def _norm(destination: str) -> str:
    raw = (destination or "").strip().lower().replace(" ", "_")
    return _ALIASES.get(raw, raw)


def _row(dest_id: str) -> dict[str, str] | None:
    for row in _DESTINATIONS:
        if row["id"] == dest_id:
            return dict(row)
    return None


def map_public() -> dict[str, Any]:
    """Belt/Control coordinate map. No probes. No secrets."""
    return {
        "control": "display-only",
        "execute": "POST /crew/assign",
        "vault": "OpenVault only",
        "law": LAW,
        "destinations": [
            {
                "id": row["id"],
                "title": row["title"],
                "status": row["status"],
                "adapter": row["adapter"],
                "honesty": row["honesty"],
            }
            for row in _DESTINATIONS
        ],
    }


def _provider(label: str) -> Any:
    from CortexOS.crew.config import resolve_providers

    needle = (label or "").strip().lower()
    for row in resolve_providers():
        if row.label.lower() == needle:
            return row
    return None


def _router_armed(label: str) -> dict[str, Any]:
    """Arming via the existing Crew router. Never returns a secret."""
    from CortexOS.crew.openvault import vault_armed_labels

    row = _provider(label)
    vault_on = label in vault_armed_labels()
    configured = bool(row is not None and row.configured)
    return {
        "armed": configured,
        "armed_via": str(getattr(row, "armed_via", "") or "") if row is not None else "",
        "vault": vault_on,
        "label": label,
        "source": str(getattr(row, "source", "") or "") if row is not None else "",
    }


def _inference_choice() -> dict[str, Any]:
    snap = chosen_public()
    return {
        "armed": snap.get("chosen") is not None,
        "chosen": snap.get("chosen"),
        "refused": snap.get("refused"),
    }


def _local_armed() -> dict[str, Any]:
    ollama = _provider("ollama")
    if ollama is not None and ollama.configured:
        return {
            "armed": True,
            "armed_via": ollama.armed_via or "env",
            "label": "ollama",
            "model": ollama.model,
        }
    compat = _provider("openai-compatible")
    base = str(getattr(compat, "api_base", "") or "") if compat is not None else ""
    loopback = "127.0.0.1" in base or "localhost" in base
    if compat is not None and compat.configured and loopback:
        return {
            "armed": True,
            "armed_via": compat.armed_via or "env",
            "label": "openai-compatible",
            "model": compat.model,
        }
    return {
        "armed": False,
        "armed_via": "",
        "label": "local_model",
        "refused": "unarmed local model (no silent fallback)",
    }


def airgpt_probe() -> dict[str, Any]:
    """Thin-client liveness. CREW_LIVE_PROBES=0 never invents a live host."""
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return {"ok": False, "url": AIRGPT_DEFAULT, "detail": "CREW_LIVE_PROBES=0"}
    url = os.environ.get("CREW_AIRGPT_URL", AIRGPT_DEFAULT).rstrip("/")
    try:
        with urllib.request.urlopen(url, timeout=0.8) as resp:
            code = int(resp.status)
            ok = 200 <= code < 500
            return {"ok": ok, "url": url, "detail": f"HTTP {code}"}
    except Exception as exc:  # noqa: BLE001 - probe must never raise into assign
        return {"ok": False, "url": url, "detail": type(exc).__name__}


def _arm_for(dest_id: str) -> dict[str, Any]:
    if dest_id == "crew":
        return _inference_choice()
    if dest_id == "local_model":
        return _local_armed()
    if dest_id in {"claude_code", "claude_app"}:
        return _router_armed("anthropic")
    if dest_id == "cursor_cloud":
        return _router_armed("cursor")
    if dest_id == "airgpt":
        probe = airgpt_probe()
        return {
            "armed": bool(probe.get("ok")),
            "armed_via": "probe" if probe.get("ok") else "",
            "label": "airgpt",
            "refused": None if probe.get("ok") else str(probe.get("detail") or "unreachable"),
            "url": probe.get("url"),
        }
    return {"armed": False, "refused": f"unknown destination '{dest_id}'"}


def catalog(*, live: bool = True) -> dict[str, Any]:
    """Operator catalog. `live` adds armed flags; belt uses live=False (no probes)."""
    dests: list[dict[str, Any]] = []
    for row in _DESTINATIONS:
        item = dict(row)
        if live:
            arm = _arm_for(row["id"])
            item["armed"] = bool(arm.get("armed"))
            item["armed_via"] = str(arm.get("armed_via") or "")
            item["refused"] = arm.get("refused")
        dests.append(item)
    return {
        "ok": True,
        "control": "display-only",
        "execute": "POST /crew/assign",
        "vault": "OpenVault only. Crew does not hold a second vault.",
        "law": LAW,
        "destinations": dests,
    }


def _refuse(detail: str, *, code: int = 409, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": False,
        "detail": detail if "no silent fallback" in detail or HUMAN_STOP in detail else (
            detail + " (no silent fallback)"
        ),
        "status_code": code,
        "law": LAW,
    }
    body.update(extra)
    return body


def _ok(**fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "law": LAW, "vault": "OpenVault only"}
    out.update(fields)
    return out


def _require_brief(brief: str) -> str:
    text = (brief or "").strip()
    if not text:
        raise AssignError("DENIED: brief required", code=400)
    return text


def _require_armed(dest_id: str) -> dict[str, Any]:
    arm = _arm_for(dest_id)
    if arm.get("armed"):
        return arm
    reason = str(arm.get("refused") or arm.get("detail") or "unarmed")
    if "no silent fallback" not in reason:
        reason = f"unarmed destination '{dest_id}': {reason} (no silent fallback)"
    raise AssignError(reason, code=409)


async def _dispatch_crew(runtime: Any, req: dict[str, Any]) -> dict[str, Any]:
    from CortexOS.crew import assign as assign_mod
    from CortexOS.crew import life

    space_id = str(req.get("space_id") or "").strip()
    if not space_id or runtime.store.get_space(space_id) is None:
        raise AssignError("unknown space", code=404)
    brief = _require_brief(str(req.get("brief") or ""))
    name = str(req.get("name") or "Assign").strip() or "Assign"
    if name.lower() == "manager":
        raise AssignError("DENIED: assign a teammate, not Manager", code=400)
    arm = _require_armed("crew")
    spawned = await runtime.operator_spawn(
        space_id,
        {"name": name, "brief": brief, "mode": life.MODE_GOAL, "goal": brief},
    )
    if spawned.get("error"):
        raise AssignError(str(spawned["error"]), code=400)
    row = spawned.get("agent")
    if not isinstance(row, dict) or not row.get("id"):
        raise AssignError("DENIED: spawn failed", code=400)
    spec = str(req.get("spec") or "").strip()
    bound: dict[str, Any] | None = None
    if spec:
        bound = assign_mod.bind(
            runtime.settings.data_dir,
            space_id=space_id,
            spec=spec,
            agent_id=str(row["id"]),
            agent_name=str(row.get("name") or name),
            title=brief[:200],
        )
        if not bound.get("ok"):
            raise AssignError(str(bound.get("detail") or "DENIED: assign failed"), code=409)
    run_id = runtime._execute_assigned(space_id, row, brief)
    return _ok(
        destination="crew",
        status="live",
        executed=True,
        agent={"id": row["id"], "name": row.get("name")},
        run_id=run_id,
        chosen=arm.get("chosen"),
        spec=(bound or {}).get("spec") if bound else None,
    )


def _dispatch_claude_code(req: dict[str, Any]) -> dict[str, Any]:
    if req.get("live_ssh") or str(req.get("lane") or "").strip().lower() in {"ssh", "live-ssh"}:
        raise AssignError(
            f"{HUMAN_STOP}: will not invent live SSH (no silent fallback)",
            code=409,
        )
    arm = _require_armed("claude_code")
    brief = _require_brief(str(req.get("brief") or ""))
    lane = str(req.get("lane") or os.environ.get("CREW_CLAUDE_CODE_LANE") or "").strip()
    return _ok(
        destination="claude_code",
        status="preview",
        executed=False,
        armed_via=arm.get("armed_via"),
        handoff={
            "kind": "claude_code",
            "lane": lane or "",
            "cli": "claude",
            "ssh": False,
            "brief": brief,
            "note": (
                "lane declared; CLI/SSH not opened"
                if lane
                else "lane undeclared; CLI/SSH not opened"
            ),
        },
    )


def _dispatch_claude_app(req: dict[str, Any]) -> dict[str, Any]:
    arm = _require_armed("claude_app")
    brief = _require_brief(str(req.get("brief") or ""))
    return _ok(
        destination="claude_app",
        status="preview",
        executed=False,
        armed_via=arm.get("armed_via"),
        handoff={
            "kind": "claude_app",
            "url": "https://claude.ai/new",
            "brief": brief,
            "note": "link/handoff only. Does not call the Claude App API.",
        },
    )


def _dispatch_cursor_cloud(req: dict[str, Any]) -> dict[str, Any]:
    if req.get("execute"):
        raise AssignError(
            f"{HUMAN_STOP}: Crew does not spawn one Cursor cloud agent. Handoff only.",
            code=409,
        )
    arm = _require_armed("cursor_cloud")
    brief = _require_brief(str(req.get("brief") or ""))
    from CortexOS.crew.openvault import cursor_model

    return _ok(
        destination="cursor_cloud",
        status="preview",
        executed=False,
        armed_via=arm.get("armed_via"),
        handoff={
            "kind": "cursor_cloud_agent",
            "prompt": brief,
            "model": cursor_model(),
            "execute": False,
            "note": "Handoff shape only. Human opens the cloud agent.",
        },
    )


def _dispatch_local(req: dict[str, Any]) -> dict[str, Any]:
    arm = _require_armed("local_model")
    brief = _require_brief(str(req.get("brief") or ""))
    return _ok(
        destination="local_model",
        status="live",
        executed=False,
        armed_via=arm.get("armed_via"),
        chosen={"label": arm.get("label"), "model": arm.get("model")},
        handoff={
            "kind": "local_model",
            "brief": brief,
            "label": arm.get("label"),
            "model": arm.get("model"),
            "note": "Local host is armed via the Crew router. Assign does not copy keys.",
        },
    )


def _dispatch_airgpt(req: dict[str, Any]) -> dict[str, Any]:
    arm = _require_armed("airgpt")
    brief = _require_brief(str(req.get("brief") or ""))
    return _ok(
        destination="airgpt",
        status="preview",
        executed=False,
        armed_via=arm.get("armed_via"),
        handoff={
            "kind": "airgpt",
            "url": arm.get("url") or AIRGPT_DEFAULT,
            "port": AIRGPT_PORT,
            "brief": brief,
            "note": "Thin client. Crew does not store an AirGPT key.",
        },
    )


async def dispatch(runtime: Any, req: dict[str, Any]) -> dict[str, Any]:
    """Route one assign. Fail-closed. Never stores API keys."""
    dest = _norm(str(req.get("destination") or ""))
    row = _row(dest)
    if row is None:
        return _refuse(f"unknown destination '{req.get('destination')}'", code=400)
    try:
        if dest == "crew":
            return await _dispatch_crew(runtime, req)
        if dest == "claude_code":
            return _dispatch_claude_code(req)
        if dest == "claude_app":
            return _dispatch_claude_app(req)
        if dest == "cursor_cloud":
            return _dispatch_cursor_cloud(req)
        if dest == "local_model":
            return _dispatch_local(req)
        if dest == "airgpt":
            return _dispatch_airgpt(req)
        return _refuse(f"unknown destination '{dest}'", code=400)
    except AssignError as exc:
        return _refuse(exc.detail, code=exc.code, destination=dest)
