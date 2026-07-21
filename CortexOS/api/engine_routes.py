"""
CortexOS/api/engine_routes.py
Netie Engine inference-plane API (E0 registry surface).

AirGPT is the driver: it probes hardware locally and POSTs the probe here;
Cortex owns the capability registry, auto-profile, and toggle state.

No from __future__ import annotations (FastAPI rule).
Pydantic models at module level.
"""
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from netie.engine import registry

router = APIRouter(prefix="/api/engine", tags=["engine"])

_STATE_FILE = Path("data") / "engine" / "config.json"


class HardwareIn(BaseModel):
    ram_gb: Optional[float] = None
    vram_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    nvidia: Dict[str, Any] = {}
    tier: Optional[str] = None
    platform: Optional[str] = None


class EngineConfigIn(BaseModel):
    backend: Optional[str] = None            # active LLM runtime; None = auto
    agents_runtime: Optional[str] = None     # cortex | langgraph | langchain
    optimizers: Optional[List[str]] = None   # explicit toggle set; None = auto
    research: bool = False                   # allow research-flagged optimizers
    hardware: Optional[HardwareIn] = None    # driver-probed hardware snapshot
    # Orchestrator cascade: user picks the primary (any BYOK/free key) from a
    # dropdown; the rest is the fallback order. Target: primary → groq →
    # openrouter → sea-lion, free tiers last. Benchmark-driven per-role ranking
    # (verifier/router/normal) lands with the bench harness.
    primary_provider: Optional[str] = None
    provider_order: Optional[List[str]] = None


def _load_state() -> Dict[str, Any]:
    try:
        if _STATE_FILE.is_file():
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = time.time()
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


@router.get("/specs")
async def engine_specs() -> Dict[str, Any]:
    """Merged capability surface — AirGPT's (i) popover reads this."""
    state = _load_state()
    hw = state.get("hardware") or None
    active = state.get("backends_active") or None
    out = registry.specs(hardware=hw, active_backends=active)
    out["config"] = {
        "backend": state.get("backend"),
        "agents_runtime": state.get("agents_runtime") or "cortex",
        "research": bool(state.get("research")),
    }
    return out


@router.get("/backends")
async def engine_backends() -> Dict[str, Any]:
    state = _load_state()
    return {
        "ok": True,
        "backends": [
            {
                "id": b.id, "name": b.name, "icon": b.icon, "tagline": b.tagline,
                "strengths": list(b.strengths), "needs_gpu": b.needs_gpu,
                "install_how": b.install_how, "port": b.port, "docs": b.docs,
                "windows_note": b.windows_note, "recommended": b.recommended,
            }
            for b in registry.BACKENDS
        ],
        "active": state.get("backends_active") or [b.id for b in registry.BACKENDS],
        "agents_runtimes": [
            {"id": "cortex", "name": "Cortex", "default": True,
             "blurb": "Governed agent engine — compliance gate, DAG runner, cost ledger."},
            {"id": "langgraph", "name": "LangGraph", "default": False,
             "blurb": "Graph agent runtime — install via marketplace, runs behind the Cortex gate."},
            {"id": "langchain", "name": "LangChain", "default": False,
             "blurb": "Chain/tool runtime — install via marketplace, runs behind the Cortex gate."},
        ],
    }


@router.post("/config")
async def engine_config(body: EngineConfigIn) -> Dict[str, Any]:
    state = _load_state()
    if body.backend is not None:
        if body.backend and body.backend not in registry.BACKEND_BY_ID and body.backend != "netie":
            return {"ok": False, "error": f"unknown backend '{body.backend}'"}
        state["backend"] = body.backend or None
    if body.agents_runtime is not None:
        state["agents_runtime"] = body.agents_runtime
    if body.optimizers is not None:
        unknown = [o for o in body.optimizers if o not in registry.OPTIMIZER_BY_ID]
        if unknown:
            return {"ok": False, "error": f"unknown optimizers {unknown}"}
        blocked = [
            o for o in body.optimizers
            if registry.OPTIMIZER_BY_ID[o].research and not body.research
        ]
        if blocked:
            return {"ok": False, "error": f"research-gated (pass research=true): {blocked}"}
        state["optimizers"] = body.optimizers
    state["research"] = bool(body.research)
    if body.primary_provider is not None:
        state["primary_provider"] = body.primary_provider[:40]
    if body.provider_order is not None:
        state["provider_order"] = [str(p)[:40] for p in body.provider_order][:12]
    if body.hardware is not None:
        state["hardware"] = body.hardware.model_dump(exclude_none=True)
    _save_state(state)
    hw = state.get("hardware") or None
    return {"ok": True, "config": {k: state.get(k) for k in
            ("backend", "agents_runtime", "optimizers", "research",
             "primary_provider", "provider_order")},
            "auto_profile": registry.auto_profile(hw)}


def register_engine_routes(app: Any) -> None:
    app.include_router(router)
