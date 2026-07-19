# P0 Research Bundle — Gate Status

**Gathered:** 2026-07-18  
**Brief:** [`../NETIE_ENGINE_UP_RESEARCH_BRIEF.md`](../NETIE_ENGINE_UP_RESEARCH_BRIEF.md)  
**Plan:** [`../../strategy/NETIE_ENGINE_UP_PLAN.md`](../../strategy/NETIE_ENGINE_UP_PLAN.md)  
**Hardware:** RTX 4070 12 GB · i5-13490F · 32 GB RAM · Win11 + Docker/WSL2

| # | Items | File | Unblocks | Gate |
|---|---|---|---|---|
| 1 | A1 + A2 | [A1_A2_mmap_pagecache.md](A1_A2_mmap_pagecache.md) | raw-mmap memory tier | **GREEN** |
| 2 | B4 | [B4_backend_offload_matrix.md](B4_backend_offload_matrix.md) | OOM-guard MVP tiering table | **GREEN** |
| 3 | D1 + D5 + D6 | [D1_D5_D6_vector_memory.md](D1_D5_D6_vector_memory.md) | personal memory default | **GREEN** |
| 4 | G1 + G2 | [G1_G2_windows_gpu_runtime.md](G1_G2_windows_gpu_runtime.md) | real backends E1 | **YELLOW** → Docker GPU smoke **passed** (4070 / driver 595.79 / `linux/28.0.4`); GREEN after `ollama run` |
| 5 | C1 + C3 | [C1_C3_turboquant_awq.md](C1_C3_turboquant_awq.md) | GPU serve defaults E2 | **GREEN** (TQ opt-in until bench) |

## Flip summary for builder

1. **rawknn** — implement `prefetch`/`evict`/`remap_to`; chunk-grow `vectors.bin`; SQLite meta+chunks.  
2. **tiering** — orchestrate vLLM OffloadingConnector + `--cpu-offload-gb` / Ollama `q8_0` / llama `-ngl` (see B4 YAML).  
3. **brain.store auto** — `N<10k → rawknn`, `<500k → sqlitevec`, else Qdrant.  
4. **E1** — Ollama native personal; vLLM/SGLang Docker WSL2 power (run G1 smokes).  
5. **E2** — AWQ 7–8B + fp8/q8_0 KV defaults; TurboQuant = registry research flag until Needle ≥ 0.95.

## Parallel build (no research wait)

- E0 registry · M0 store protocol · H1 dms-v2 wiring — already unblocked.

## Next gather (P1)

C2, D2–D3, E1–E3, F1/F3/F4, B1–B2, G3 — after P0 gates folded into phase specs.
