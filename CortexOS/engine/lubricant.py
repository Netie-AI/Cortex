"""WD40 thesis — Cortex is the lubricant, not another inference kernel.

Answer to: "are we just layering on top of vLLM/Ollama/SGLang, or competing?"

**We layer on purpose.** Those engines win at raw token generation (PagedAttention,
RadixAttention, GGUF simplicity). Cortex wins at making them usable together:
route, govern, compact, never OOM, never make the user pick a backend.

Cross-OS: Windows/macOS/Linux. Cross-function: orchestration + retrieval +
memory + ontology + routines + app package. Best efficiency = best *system*
efficiency (TTFT under governance + cost + safety), not claiming to beat
cuBLAS kernels inside vLLM.
"""

from __future__ import annotations

THESIS = {
    "role": "lubricant",
    "metaphor": "WD-40 for AI stacks",
    "competes_with": [],
    "orchestrates": ["ollama", "vllm", "sglang", "llamacpp", "colibri"],
    "wins_on": [
        "auto backend + middleware selection from hardware",
        "governed actions / ledger / RBAC identical for human and agent",
        "DAG + race router + JEPA family gate (architecture efficiency)",
        "context engineering + deferred tools (token efficiency)",
        "cross-OS install: native where possible, Docker where required",
        "idiot-proof defaults: one call, no dropdowns required",
    ],
    "does_not_rewrite": [
        "PagedAttention / RadixAttention CUDA kernels",
        "GGUF loaders",
        "MoE expert streaming inside Colibri",
    ],
    "may_rewrite_later_in_rust": [
        "scoreboard embed + family match hot path",
        "step journal concurrent writes",
        "zip-slip / secrets scan for app packages",
        "optional CUDA probe helpers (not full inference)",
    ],
    "user_ux": "users_are_idiots_by_design",
    "ux_rules": [
        "never ask backend vs optimizer unless Advanced is open",
        "never show research toggles until bench_gate passed",
        "one button: Just Works -> auto_profile + apply",
        "failures speak human: 'GPU missing - using Ollama' not stack traces",
        "wedged ports are health-checked as process+HTTP, not port alone",
    ],
}


def thesis() -> dict:
    return dict(THESIS)
