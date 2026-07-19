"""E0 — Netie Engine capability registry (registry-first unblocker).

Single source of truth for what the unified runtime *can* do: which backends
exist, which optimizers can be toggled, and the merged capability/specs surface
that AirGPT's Hosting `(i)` popover reads (replacing AirGPT's local
`netie_engine.py` shim).

Design rules (from the plan):
  * Every optimizer is toggleable but ships an auto default per hardware.
  * `research=True` optimizers are opt-in and cannot become a *default* until a
    bench gate (docs/research + bench harness) passes → `bench_gate`.
  * No hard third-party deps here — pure descriptors + policy. Backends/adapters
    import THIS, never the other way around.

Import as ``netie.engine.registry``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

OptimizerCategory = Literal[
    "kv_quant", "eviction", "weight_quant", "paging", "moe", "context", "tiering"
]


# ─── Descriptors ──────────────────────────────────────────────────────────────
@dataclass(slots=True, frozen=True)
class BackendDescriptor:
    id: str
    name: str
    icon: str
    tagline: str
    strengths: tuple[str, ...]
    serving: str                    # openai-compat serving family
    needs_gpu: bool
    install_how: Literal["native", "docker", "vendored"]
    base_url_env: str
    port: int
    docs: str
    windows_note: str | None = None
    recommended: bool = False


@dataclass(slots=True, frozen=True)
class OptimizerDescriptor:
    id: str
    label: str
    category: OptimizerCategory
    detail: str
    default: bool = False           # on in the auto profile?
    research: bool = False          # opt-in behind the research flag?
    bench_gate: str = ""            # what must pass before it may become default
    backends: tuple[str, ...] = ()  # which backends can host it ("" = engine-level)


# ─── Catalog (mirrors AirGPT netie_engine.py; Cortex is now the source) ────────
BACKENDS: tuple[BackendDescriptor, ...] = (
    BackendDescriptor(
        "ollama", "Ollama", "🦙", "Dead-simple GGUF runtime — one command, always fits.",
        ("GGUF Q4/Q5/Q8", "auto pull", "CPU or GPU", "no config"),
        "ollama", False, "native", "OLLAMA_BASE_URL", 11434, "https://ollama.com",
        recommended=True,
    ),
    BackendDescriptor(
        "vllm", "vLLM", "⚡", "Throughput king — PagedAttention KV, continuous batching.",
        ("PagedAttention KV", "continuous batching", "FP8/AWQ/GPTQ", "OpenAI API"),
        "vllm", True, "docker", "VLLM_BASE_URL", 8001, "https://docs.vllm.ai",
        windows_note="No native Windows build — Docker + WSL2 GPU passthrough.",
    ),
    BackendDescriptor(
        "sglang", "SGLang", "🧬", "RadixAttention prefix cache — fastest for shared prompts/agents.",
        ("RadixAttention", "structured output", "FP8/AWQ", "OpenAI API"),
        "sglang", True, "docker", "SGLANG_BASE_URL", 30000, "https://docs.sglang.ai",
        windows_note="Docker + WSL2 GPU passthrough.",
    ),
    BackendDescriptor(
        "colibri", "Colibri", "🐦", "Frontier MoE larger than VRAM — streams experts from disk.",
        ("disk-streamed MoE", "int4 experts", "runs > VRAM", "host role"),
        "colibri", False, "vendored", "COLIBRI_BASE_URL", 8002,
        "https://github.com/JustVugg/colibri",
    ),
    BackendDescriptor(
        "llamacpp", "llama.cpp", "🐫", "GGUF + TurboQuant KV on modest GPUs.",
        ("GGUF", "TurboQuant KV", "partial offload", "mmap weights"),
        "llamacpp", False, "native", "LLAMACPP_BASE_URL", 8080,
        "https://github.com/ggml-org/llama.cpp",
    ),
)

OPTIMIZERS: tuple[OptimizerDescriptor, ...] = (
    OptimizerDescriptor(
        "paged_attention", "PagedAttention", "paging",
        "Block KV cache, near-zero fragmentation. Orthogonal to quant — always safe.",
        default=True, backends=("vllm", "sglang"),
    ),
    OptimizerDescriptor(
        "turboquant", "TurboQuant (KV)", "kv_quant",
        "Global-rotation KV quant; best accuracy (Needle ~0.997). Proposed GPU default.",
        default=True, bench_gate="C1: confirm llama.cpp/vLLM support + reproduce Needle>=0.95",
        backends=("llamacpp",),
    ),
    OptimizerDescriptor(
        "awq", "AWQ weights", "weight_quant",
        "Activation-aware 3–4-bit weights; stacks with KV quant. GPU serve default.",
        default=True, bench_gate="C3: AWQ checkpoint exists + fits 12GB", backends=("vllm", "sglang"),
    ),
    OptimizerDescriptor("gptq", "GPTQ weights", "weight_quant",
        "Calibrated weight quant; fallback when AWQ unavailable.", backends=("vllm", "sglang")),
    OptimizerDescriptor("kivi", "KIVI", "kv_quant",
        "2-bit per-channel/token KV; more compression, Needle ~0.981.",
        research=True, bench_gate="C2 bench vs TurboQuant baseline"),
    OptimizerDescriptor("snapkv", "SnapKV", "eviction",
        "Drop low-attention tokens; up to 3.6× faster, lossy (Needle ~0.858).",
        research=True, bench_gate="C2 bench recall floor"),
    OptimizerDescriptor("rotorquant", "RotorQuant", "kv_quant",
        "Clifford-rotor block rotations; 10–19× vs cuBLAS matmul.",
        research=True, bench_gate="C2 reproduce speedup + recall"),
    OptimizerDescriptor("dsa", "DeepSeek DSA context", "context",
        "Sparse-context select/compress/sliding (AirGPT ditch/index analog).", default=True),
    OptimizerDescriptor("moe_stream", "MoE expert streaming", "moe",
        "Stream inactive experts from NVMe (Colibri).", backends=("colibri",)),
    OptimizerDescriptor("tiering", "Predictive memory tiering", "tiering",
        "MVP: orchestrate backend offload knobs + NVMe KV page cache + prefetch (no-OOM).",
        default=True, bench_gate="E4: model+KV 2–3× VRAM runs; TTFT within gate",
        backends=("vllm", "llamacpp", "colibri")),
)

BACKEND_BY_ID = {b.id: b for b in BACKENDS}
OPTIMIZER_BY_ID = {o.id: o for o in OPTIMIZERS}


# ─── Auto profile + specs aggregator ──────────────────────────────────────────
def auto_profile(hardware: dict | None = None) -> dict:
    """Pick a sensible backend + default optimizer set from detected hardware.

    `hardware` shape mirrors AirGPT `marketplace.effective_hardware()`:
      {ram_gb, vram_gb, disk_free_gb, nvidia:{present}, tier, platform}.
    """
    hw = hardware or {}
    vram = float(hw.get("vram_gb") or 0)
    has_gpu = bool((hw.get("nvidia") or {}).get("present")) or vram >= 6
    if not has_gpu:
        backend = "ollama"          # CPU GGUF, always fits
    elif vram >= 10:
        backend = "vllm"            # AWQ 7–8B fits 12GB
    else:
        backend = "ollama"
    defaults = tuple(
        o.id for o in OPTIMIZERS
        if o.default and (not o.backends or backend in o.backends)
    )
    return {"backend": backend, "optimizers": defaults, "has_gpu": has_gpu, "vram_gb": vram}


def specs(hardware: dict | None = None, active_backends: list[str] | None = None) -> dict:
    """Merged capability surface for AirGPT's `(i)` popover / `/api/engine/specs`."""
    active = [b for b in (active_backends or [b.id for b in BACKENDS]) if b in BACKEND_BY_ID]
    opts = [o for o in OPTIMIZERS if not o.backends or any(b in o.backends for b in active)]
    by_cat: dict[str, list[str]] = {}
    for o in opts:
        by_cat.setdefault(o.category, []).append(o.label)
    return {
        "engine": "Netie Engine",
        "summary": "One runtime. Best backend per model, never OOM.",
        "profile": auto_profile(hardware),
        "backends": [b.id for b in BACKENDS if b.id in active],
        "optimizations": [
            {"category": c, "items": v} for c, v in sorted(by_cat.items())
        ],
        "research_toggles": [o.id for o in OPTIMIZERS if o.research],
        "gated": [{"id": o.id, "gate": o.bench_gate} for o in OPTIMIZERS if o.bench_gate],
    }


def self_check() -> dict:
    """Cheap import/shape sanity — no I/O, safe to call anywhere."""
    prof = auto_profile({"vram_gb": 12, "nvidia": {"present": True}})
    sp = specs()
    assert prof["backend"] in BACKEND_BY_ID
    assert sp["engine"] == "Netie Engine" and sp["optimizations"]
    return {"ok": True, "backends": len(BACKENDS), "optimizers": len(OPTIMIZERS), "auto": prof}


if __name__ == "__main__":  # pragma: no cover
    import json
    print(json.dumps(self_check(), indent=2))
