"""Idiot-proof Just Works planner — pick backend + middleware, no user thinking.

Users should enjoy the product; Cortex chooses Ollama vs vLLM vs SGLang and
applies only safe default optimizers (research boosters stay gated until bench).
"""

from __future__ import annotations

import platform
from typing import Any

from CortexOS.engine import registry
from CortexOS.engine.lubricant import thesis


def detect_platform() -> dict[str, Any]:
    sys = platform.system().lower()
    return {
        "system": sys,
        "machine": platform.machine().lower(),
        "cross_os": True,
        "docker_recommended_for": [
            b.id for b in registry.BACKENDS if b.install_how == "docker"
        ],
        "native_friendly": [
            b.id for b in registry.BACKENDS if b.install_how == "native"
        ],
        "windows_gpu_note": (
            "vLLM/SGLang need Docker+WSL2 GPU passthrough on Windows; "
            "Ollama/llama.cpp run native."
            if sys.startswith("win")
            else None
        ),
    }


def just_works(
    hardware: dict[str, Any] | None = None,
    *,
    research: bool = False,
    prefer: str | None = None,
) -> dict[str, Any]:
    """One-shot plan a human never has to edit.

    Returns apply-ready config + human reasons (idiot-proof copy).
    """
    hw = dict(hardware or {})
    plat = detect_platform()
    # Driver/AirGPT may report OS; prefer that over host detect (tests + remote probe).
    reported = str(hw.get("platform") or "").strip().lower()
    if reported:
        plat = {**plat, "system": reported}

    profile = registry.auto_profile(hw)
    backend = prefer if prefer in registry.BACKEND_BY_ID else profile["backend"]

    # Cross-OS override: Windows without explicit Docker preference → prefer native.
    if plat["system"].startswith("win") and backend in ("vllm", "sglang"):
        if not hw.get("allow_docker_gpu"):
            backend = "ollama"
            reason_os = "Windows without allow_docker_gpu - Ollama (native)."
        else:
            reason_os = "Windows + allow_docker_gpu - keeping GPU Docker backend."
    else:
        reason_os = f"Platform {plat['system']}: backend {backend}."

    opts = [
        o.id
        for o in registry.OPTIMIZERS
        if (not o.backends or backend in o.backends)
        and o.default
        and (research or not o.research)
    ]
    # Boosters: research-flagged never auto-on.
    boosters = [
        {
            "id": o.id,
            "label": o.label,
            "gated": True,
            "bench_gate": o.bench_gate,
            "why_not_auto": "research until bench gate passes",
        }
        for o in registry.OPTIMIZERS
        if o.research and (not o.backends or backend in o.backends)
    ]

    human = [
        reason_os,
        f"Selected {registry.BACKEND_BY_ID[backend].name}: {registry.BACKEND_BY_ID[backend].tagline}",
        f"Enabled {len(opts)} safe middleware(s): {', '.join(opts) or 'none'}.",
    ]
    if boosters:
        human.append(
            f"{len(boosters)} research booster(s) available but OFF "
            f"(TurboQuant/KIVI/... need a green bench first)."
        )
    if profile["has_gpu"] and backend == "ollama":
        human.append(
            "GPU detected but staying on Ollama for simplicity - flip Advanced to vLLM/SGLang."
        )
    if not profile["has_gpu"]:
        human.append("No GPU - Ollama/CPU path. You don't need to install CUDA.")

    return {
        "ok": True,
        "mode": "just_works",
        "thesis": thesis()["role"],
        "config": {
            "backend": backend,
            "optimizers": opts,
            "research": bool(research),
            "agents_runtime": "cortex",
            "architecture_preset": "minimal",
        },
        "boosters_available": boosters,
        "platform": plat,
        "profile": profile,
        "human": human,
        "apply_hint": "POST /api/engine/config with this config (or POST /api/engine/just-works?apply=1)",
    }


def compare_backends_narrative() -> list[dict[str, str]]:
    """Cross-comparison card for UI — efficiency roles, not fake tok/s claims."""
    return [
        {
            "id": b.id,
            "name": b.name,
            "best_for": b.tagline,
            "strengths": ", ".join(b.strengths),
            "cortex_role": "inference backend - Cortex routes/governs on top",
            "cross_os": b.windows_note or "native or docker per install_how",
        }
        for b in registry.BACKENDS
    ]
