# WD-40: Cortex as lubricant over inference engines

**Date:** 2026-07-26  
**Verdict:** We **layer on purpose**. Cortex is not competing with vLLM / Ollama / SGLang CUDA kernels.

## Positioning

| Layer | Owner | Job |
|-------|--------|-----|
| Token generation | ollama, vLLM, SGLang, llama.cpp, Colibri | Fast / correct decode |
| System efficiency | **Cortex** | Pick backend, govern, compact context, route races, never OOM the user |
| Research boosters | TurboQuant, KIVI, … | Gated until `bench_gate` green |

Metaphor: **WD-40** — make every stack slide; do not invent a new bolt.

## Shipped this slice

- `CortexOS/engine/lubricant.py` — thesis (competes_with=[])
- `CortexOS/engine/just_works.py` — one-shot idiot-proof plan
- `CortexOS/engine/bakeoff.py` + `python -m bench.engine_bakeoff` — soft probe
- APIs: `GET /api/engine/thesis`, `POST /api/engine/just-works`, `POST /api/engine/bakeoff`
- PARKING **P20** — Rust for Cortex hot paths only (not PagedAttention)

## UX rule

Users are idiots **by design**: no backend dropdown until Advanced; human failure copy; Just Works applies safe defaults.

## Not claimed

Bakeoff scores today = reachability + health_ms, **not** full tok/s. Full gen benches need a live model pull.
