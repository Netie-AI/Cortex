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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from packs.dms.security.api_auth import Caller, require_role, role_at_least
from netie.engine import registry
from CortexOS.execution import architecture_presets
from CortexOS.execution.preset_router import plan_for_request
from CortexOS.execution.run_plan import execute_run_plan
from CortexOS.paths import data_path

router = APIRouter(prefix="/api/engine", tags=["engine"])

_STATE_FILE = data_path("engine", "config.json")


class HardwareIn(BaseModel):
    ram_gb: Optional[float] = None
    vram_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    nvidia: Dict[str, Any] = {}
    tier: Optional[str] = None
    platform: Optional[str] = None
    allow_docker_gpu: Optional[bool] = None  # Windows WSL2/Docker GPU passthrough OK


class EngineConfigIn(BaseModel):
    backend: Optional[str] = None            # active LLM runtime; None = auto
    agents_runtime: Optional[str] = None     # cortex | langgraph | langchain
    architecture_preset: Optional[str] = None  # dag|sequential|langgraph|minimal|rag|…
    optimizers: Optional[List[str]] = None   # explicit toggle set; None = auto
    research: bool = False                   # allow research-flagged optimizers
    hardware: Optional[HardwareIn] = None    # driver-probed hardware snapshot
    # Orchestrator cascade: user picks the primary (any BYOK/free key) from a
    # dropdown; the rest is the fallback order. Target: primary → groq →
    # openrouter → sea-lion, free tiers last. Benchmark-driven per-role ranking
    # (verifier/router/normal) lands with the bench harness.
    primary_provider: Optional[str] = None
    provider_order: Optional[List[str]] = None


class EngineRunIn(BaseModel):
    prompt: Optional[str] = None
    architecture_preset: Optional[str] = None
    dag: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    action_id: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class JustWorksIn(BaseModel):
    hardware: Optional[HardwareIn] = None
    research: bool = False
    prefer: Optional[str] = None
    apply: bool = False  # if true, persist as engine config (admin)


class BakeoffIn(BaseModel):
    hardware: Optional[HardwareIn] = None
    write_report: bool = True


class PatternRecommendIn(BaseModel):
    prompt: Optional[str] = None
    signals: Dict[str, Any] = Field(default_factory=dict)
    tool_count: int = 0


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
async def engine_specs(caller: Caller = Depends(require_role("viewer"))) -> Dict[str, Any]:
    """Merged capability surface — AirGPT's (i) popover reads this."""
    _ = caller
    state = _load_state()
    hw = state.get("hardware") or None
    active = state.get("backends_active") or None
    out = registry.specs(hardware=hw, active_backends=active)
    out["config"] = {
        "backend": state.get("backend"),
        "agents_runtime": state.get("agents_runtime") or "cortex",
        "architecture_preset": architecture_presets.normalize_preset(
            state.get("architecture_preset")
        ),
        "research": bool(state.get("research")),
    }
    return out


@router.get("/backends")
async def engine_backends(caller: Caller = Depends(require_role("viewer"))) -> Dict[str, Any]:
    _ = caller
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
        "architecture_presets": architecture_presets.catalog(),
        "coordination_patterns_hint": (
            "GET /api/engine/coordination-patterns — Anthropic multi-agent "
            "pattern catalog mapped to Cortex runners"
        ),
    }


@router.post("/config")
async def engine_config(
    body: EngineConfigIn,
    caller: Caller = Depends(require_role("admin")),
) -> Dict[str, Any]:
    _ = caller
    state = _load_state()
    if body.backend is not None:
        if body.backend and body.backend not in registry.BACKEND_BY_ID and body.backend != "netie":
            return {"ok": False, "error": f"unknown backend '{body.backend}'"}
        state["backend"] = body.backend or None
    if body.agents_runtime is not None:
        state["agents_runtime"] = body.agents_runtime
    if body.architecture_preset is not None:
        state["architecture_preset"] = architecture_presets.normalize_preset(
            body.architecture_preset
        )
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
        try:
            from CortexOS.execution import workflow_runner

            workflow_runner.set_hardware(state["hardware"])
        except Exception:
            pass
    _save_state(state)
    hw = state.get("hardware") or None
    return {"ok": True, "config": {k: state.get(k) for k in
            ("backend", "agents_runtime", "architecture_preset", "optimizers", "research",
             "primary_provider", "provider_order")},
            "run_plan": architecture_presets.resolve_runner(state.get("architecture_preset")),
            "auto_profile": registry.auto_profile(hw)}


@router.post("/run")
async def engine_run(
    body: EngineRunIn,
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    """Resolve the selected architecture and dispatch it to an existing runner."""
    if body.action_id and not role_at_least(caller.role, "steward"):
        raise HTTPException(status_code=403, detail="Actions require role 'steward' or higher")
    request_body = body.model_dump(exclude_none=True)
    plan = plan_for_request(body.architecture_preset, request_body)
    try:
        return await execute_run_plan(plan, request_body, caller=caller)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/thesis")
async def engine_thesis(caller: Caller = Depends(require_role("viewer"))) -> Dict[str, Any]:
    """WD-40 positioning — Cortex lubricates backends; it does not replace them."""
    _ = caller
    from CortexOS.engine.lubricant import thesis
    from CortexOS.engine.just_works import compare_backends_narrative

    return {"ok": True, "thesis": thesis(), "backends": compare_backends_narrative()}


@router.post("/just-works")
async def engine_just_works(
    body: JustWorksIn,
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    """Idiot-proof one-shot: pick backend + safe middleware. Optional apply (admin)."""
    from CortexOS.engine.just_works import just_works

    hw = body.hardware.model_dump(exclude_none=True) if body.hardware else (_load_state().get("hardware") or {})
    plan = just_works(hw, research=body.research, prefer=body.prefer)
    if body.apply:
        if not role_at_least(caller.role, "admin"):
            raise HTTPException(status_code=403, detail="apply requires admin")
        state = _load_state()
        state.update(plan["config"])
        if body.hardware is not None:
            state["hardware"] = hw
        _save_state(state)
        plan["applied"] = True
    else:
        plan["applied"] = False
    return plan


@router.post("/bakeoff")
async def engine_bakeoff(
    body: BakeoffIn,
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    """Probe ollama/vllm/sglang soft-fail; recommend best live backend under Just Works."""
    _ = caller
    from CortexOS.engine.bakeoff import run_bakeoff, write_report

    hw = body.hardware.model_dump(exclude_none=True) if body.hardware else (_load_state().get("hardware") or {})
    report = run_bakeoff(hardware=hw)
    if body.write_report:
        write_report(report)
    return report


@router.get("/coordination-patterns")
async def engine_coordination_patterns(
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    """Anthropic five-pattern catalog + Cortex gap matrix (decision surface only)."""
    _ = caller
    from CortexOS.execution import coordination_patterns

    return {
        "ok": True,
        "patterns": coordination_patterns.catalog(),
        "gap_matrix": coordination_patterns.gap_matrix(),
        "note": (
            "Recommends patterns; does not spawn a third orchestrator. "
            "Execution stays on dag_runner / workflow_runner / AGENT_TASK."
        ),
        "sources": [
            "https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them",
            "skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md",
        ],
    }


@router.post("/coordination-patterns/recommend")
async def engine_recommend_pattern(
    body: PatternRecommendIn,
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    """Recommend the simplest coordination pattern for a prompt / signal set."""
    _ = caller
    from CortexOS.execution import coordination_patterns

    extras = dict(body.signals or {})
    if body.tool_count:
        extras["tool_count"] = body.tool_count
    if body.prompt:
        rec = coordination_patterns.recommend_from_prompt(body.prompt, extras=extras)
    else:
        rec = coordination_patterns.recommend(extras)
    return {"ok": True, "recommendation": rec.as_dict()}


def register_engine_routes(app: Any) -> None:
    app.include_router(router)
