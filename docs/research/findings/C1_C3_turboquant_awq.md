# C1 + C3 — TurboQuant readiness + AWQ/GPTQ availability

**Date:** 2026-07-18  
**Gate:** Unblocks GPU serve defaults (E2 auto profile)  
**Hardware context:** RTX 4070 12 GB  
**Status:** RESEARCH COMPLETE

---

## C1 — TurboQuant

### Readiness matrix

| Surface | Status | Flags / notes |
|---|---|---|
| **Paper / Google** | Published | [arXiv:2504.19874](https://arxiv.org/abs/2504.19874), [Google Research blog](https://research.google/blog/turboquant-redefining-ai-efficiency-with-extreme-compression/) |
| **llama.cpp mainline** | **Not merged** (as of research date) | Discussion [#20969](https://github.com/ggml-org/llama.cpp/discussions/20969); PR activity (e.g. #21307) — policy/AI-review churn; treat as unstable |
| **llama.cpp forks** | **Usable** | `TheTom/turboquant_plus`: `--cache-type-k turbo3 --cache-type-v turbo3` (also turbo2/turbo4); `Keyvanhardani/turboquant-ggml` uses `turbo3_0` naming |
| **Ollama** | **No** TurboQuant types | Only `f16` / `q8_0` / `q4_0` via `OLLAMA_KV_CACHE_TYPE` |
| **vLLM** | **Emerging in latest docs** | `--kv-cache-dtype` choices include `turboquant_3bit_nc`, `turboquant_4bit_nc`, `turboquant_k3v4_nc`, `turboquant_k8v4` ([latest engine args](https://docs.vllm.ai/en/latest/configuration/engine_args/)) — **pin image tag before defaulting** |
| **Needle 0.997** | Paper / marketing claim | Community PPL / speed reports on #20969; **not** treated as locally reproduced here — E2 bench gate still required (Needle ≥ 0.95) |
| **License** | Paper: research publication; Google blog | Implementation licenses follow respective forks / vLLM Apache-2.0 — do not assume patent-safe for commercial without counsel |

### AirGPT alignment

`F:\AirGPT\docs\local-inference.md` already targets:

```text
--cache-type-k turbo3 --cache-type-v turbo3
```

with note to track #20969 until mainline.

### E2 default recommendation (revised honesty)

| Profile | KV default now | Promote TurboQuant when |
|---|---|---|
| **Personal (Ollama)** | `q8_0` + Flash Attention | Ollama exposes turbo types |
| **Personal (llama.cpp)** | `q8_0` mainline; optional **research** toggle → turbo fork | Fork smoke + Needle bench pass |
| **Power (vLLM Docker)** | `fp8` safe default; **try** `turboquant_3bit_nc` if tag supports | Image tag verified + Needle ≥ 0.95 on 16k |

**Verdict:** TurboQuant is **not** yet a no-asterisk default for all backends. Keep as `engine.kv=turboquant` **capability** in registry; auto-profile uses fp8/q8_0 until mainline+bench green. Plan's "TurboQuant auto GPU KV default" stays the *target*, gated by C1 bench.

---

## C3 — AWQ / GPTQ model availability (12 GB)

### Model → quant → VRAM (approximate)

| Model | AWQ checkpoint | GPTQ | vLLM / SGLang | ~Weights VRAM (4-bit) | Fits 12 GB? |
|---|---|---|---|---|---|
| **Qwen3-8B** | [Qwen/Qwen3-8B-AWQ](https://huggingface.co/Qwen/Qwen3-8B-AWQ) | community / AngelSlim | vLLM ≥0.8.5, SGLang ≥0.4.6.post1 (per model card) | ~5–6 GB | **Yes** @ 8–16k ctx; use fp8/turbo KV for longer |
| **Qwen2.5-7B** | [Qwen/Qwen2.5-7B-Instruct-AWQ](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-AWQ) | official/community GPTQ variants | Yes (`vllm serve …`) | ~5 GB | **Yes** — primary marketplace default |
| **Qwen2.5-14B** | [Qwen/Qwen2.5-14B-Instruct-AWQ](https://huggingface.co/Qwen/Qwen2.5-14B-Instruct-AWQ) | available | Yes | ~8–9 GB | **Tight** — short ctx only; prefer GGUF Q4 / cpu-offload / hide as "power trial" |
| **Llama-3.1-8B** | community AWQ (e.g. various HF `*-AWQ`) | common | Yes | ~5–6 GB | **Yes** — prefer well-known AWQ repos; Meta license |
| **DeepSeek-R1-Distill** | e.g. [drawais/DeepSeek-R1-Distill-Llama-8B-AWQ-INT4](https://huggingface.co/drawais/DeepSeek-R1-Distill-Llama-8B-AWQ-INT4); AngelSlim INT4-AWQ for Qwen distill | AngelSlim INT4-GPTQ | Yes | ~5–6 GB (8B) | **Yes** for 7B/8B distill; 14B AWQ tight like Qwen2.5-14B |

**Rule of thumb:** 7–8B AWQ ≈ 5–6 GB weights → **~4–6 GB left for KV** on 12 GB after fragmentation → default `--max-model-len 8192` or 16384 with KV quant.

### Marketplace facet ("supported by my device")

```text
device_class: rtx_4070_12gb
prefer:
  - Qwen/Qwen2.5-7B-Instruct-AWQ
  - Qwen/Qwen3-8B-AWQ
  - DeepSeek-R1-Distill-*-7B/8B AWQ (curated ID)
  - Llama-3.1-8B Instruct AWQ (curated ID)
soft_hide_or_warn:
  - *-14B-AWQ  (ctx_cap: 4096, warn: tight_vram)
quant_default: awq
kv_default_power: fp8
kv_default_personal: q8_0
kv_research: turboquant
```

---

## Back to builder

| Gate question | Answer |
|---|---|
| Ready-to-use TurboQuant? | **Partial** — forks + vLLM latest dtypes; not Ollama; not llama.cpp mainline |
| Which backend for TQ? | Prefer **vLLM** (if tag has turboquant_*) or **llama.cpp fork**; registry flag `research=True` until bench |
| AWQ for target models? | **Yes** for 7–8B Qwen / distill; 14B optional/tight |
| E2 auto profile | **AWQ weights + fp8/q8_0 KV + PagedAttention**; TurboQuant opt-in → default after Needle gate |

**Gate: GREEN for GPU serve defaults** with the honesty revision above (TQ not silent-default until verified).
