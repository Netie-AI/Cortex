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
| 4 | G1 + G2 | [G1_G2_windows_gpu_runtime.md](G1_G2_windows_gpu_runtime.md) | real backends E1 | **GREEN** (2026-07-19: WSL `nvidia-smi` ✓ CUDA 13.2 · `ollama run` ✓ 100% GPU, 172.6 tok/s warm) |
| 5 | C1 + C3 | [C1_C3_turboquant_awq.md](C1_C3_turboquant_awq.md) | GPU serve defaults E2 | **GREEN** (TQ opt-in until bench) |

**ALL FIVE P0 GATES GREEN — 2026-07-19.**

## Flip summary for builder — status 2026-07-19

1. **rawknn** — ✅ DONE: `CortexOS/memory/stores/rawknn.py` (D6 layout + norms.bin, prefetch/evict best-effort, SQL prefilter + top-k-only fetch). On-box: 4.3 ms @ 10k, 76.8 ms @ 100k warm exact. Chunk-grow + compaction still open.
2. **tiering** — registry descriptor updated with the B4 ladder; orchestration manager still open (E4).
3. **brain.store auto** — ✅ DONE: `BRUTE_FORCE_MAX=100_000` (D5), `rawknn ≤100k → sqlitevec ≤500k (int8) → qdrant`.
4. **E1** — smokes green; Ollama serving locally (qwen2.5:0.5b now, qwen3:8b pulling); install managers still open.
5. **E2** — ✅ registry: `kv_fp8` (vLLM/SGLang) + `kv_q8_0` (llama.cpp/Ollama) are defaults; TurboQuant demoted to research toggle (K4/V3 note kept).

## Parallel build (no research wait)

- E0 registry · M0 store protocol · H1 dms-v2 wiring — already unblocked.

## Next gather (P1)

C2, D2–D3, E1–E3, F1/F3/F4, B1–B2, G3 — after P0 gates folded into phase specs.
