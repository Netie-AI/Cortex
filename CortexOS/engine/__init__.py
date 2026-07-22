"""Netie Engine — inference plane (import as ``netie.engine``).

The selectable unified runtime: vLLM / SGLang / Ollama / Colibri / llama.cpp
backends + toggleable optimizers (KV-quant, eviction, weight-quant, paging, MoE,
tiering). See docs/strategy/NETIE_ENGINE_UP_PLAN.md and
docs/research/NETIE_ENGINE_UP_RESEARCH_BRIEF.md.

E0 (registry-first): this package defines the capability registry that AirGPT's
Hosting UI reads. Backends/optimizers plug into these descriptors as their
research gates (in the brief) turn green.
"""
