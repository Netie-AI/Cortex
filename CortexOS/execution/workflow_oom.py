"""Pre-fanout concurrency gate — shrink parallel width before local models OOM.

Hardware shape matches AirGPT ``marketplace.effective_hardware`` /
Cortex ``engine.registry.auto_profile``:
  {ram_gb, vram_gb, nvidia:{present}, tier, platform}
"""

from __future__ import annotations

from typing import Any, Mapping


DEFAULT_MAX_PARALLEL = 4
#: Rough concurrent-agent VRAM budget (GB) when serving a mid local model.
_VRAM_PER_AGENT_GB = 2.5
_RAM_PER_AGENT_GB = 3.0


def resolve_max_parallel(
    hardware: Mapping[str, Any] | None = None,
    *,
    requested: int | None = None,
    prefer_cloud_spill: bool = True,
) -> dict[str, Any]:
    """Return concurrency + spill policy for one workflow phase layer.

    When VRAM/RAM cannot host ``requested`` concurrent local completions we
    shrink ``max_parallel`` and, if ``prefer_cloud_spill``, mark high-effort
    agents to route via cloud BYOK (OpenVault-backed) instead of dying on OOM.
    """
    want = max(1, int(requested or DEFAULT_MAX_PARALLEL))
    hw = dict(hardware or {})
    vram = float(hw.get("vram_gb") or 0)
    ram = float(hw.get("ram_gb") or 0)
    has_gpu = bool((hw.get("nvidia") or {}).get("present")) or vram >= 6

    if not hw:
        # No probe yet — stay conservative; cloud spill still allowed.
        return {
            "max_parallel": min(want, DEFAULT_MAX_PARALLEL),
            "spill_high_effort_to_cloud": prefer_cloud_spill,
            "reason": "no_hardware_probe",
            "has_gpu": False,
            "vram_gb": 0.0,
        }

    if has_gpu and vram > 0:
        by_vram = max(1, int(vram // _VRAM_PER_AGENT_GB))
        cap = min(want, by_vram, DEFAULT_MAX_PARALLEL + 2)
        spill = prefer_cloud_spill and cap < want
        return {
            "max_parallel": cap,
            "spill_high_effort_to_cloud": spill,
            "reason": f"vram={vram:.1f}gb -> {cap} concurrent",
            "has_gpu": True,
            "vram_gb": vram,
        }

    # CPU / mmap path — parallel local completions thrash RAM hard.
    by_ram = max(1, int(ram // _RAM_PER_AGENT_GB)) if ram else 1
    cap = min(want, by_ram, 2)
    return {
        "max_parallel": cap,
        "spill_high_effort_to_cloud": prefer_cloud_spill,
        "reason": f"cpu_ram={ram:.1f}gb -> {cap} concurrent",
        "has_gpu": False,
        "vram_gb": vram,
    }


def prefer_backend(hardware: Mapping[str, Any] | None = None) -> str:
    """Hint which local engine fits shared-prefix workflow phases best."""
    from CortexOS.engine.registry import auto_profile

    profile = auto_profile(dict(hardware or {}))
    backend = str(profile.get("backend") or "ollama")
    # RadixAttention shines when sibling agents share system/tool instructions.
    if backend in ("sglang", "vllm"):
        return backend
    return backend
