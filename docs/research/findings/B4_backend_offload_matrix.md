# B4 — Backend-Native Offload Knobs (MVP Substrate)

**Section:** B4 (NETIE Engine Up Research Brief) · **Priority:** P0 · **Gate:** unblocks OOM-guard MVP
**Date:** 2026-07-18
**Hardware target:** RTX 4070 (12 GB VRAM) · i5-13490F (16 threads) · 32 GB RAM · Windows 11 + Docker/WSL2 · ~200 GB free C: + multi-TB NVMe
**Scope:** exact flags/env vars per backend, *what each actually offloads* (weights vs KV vs experts), the limit/notes, and a source URL. No invented flags — anything not confirmed against official docs/source is marked **UNVERIFIED**.

---

## TL;DR (for `netie.engine.tiering`)

- **Weights → CPU RAM:** native in every backend (vLLM `--cpu-offload-gb`, SGLang `--cpu-offload-gb`/`--offload-mode`, llama.cpp `-ngl` partial, Ollama `num_gpu`). This is the "load a model bigger than 12 GB VRAM" lever.
- **KV → CPU RAM:** native everywhere (vLLM `OffloadingConnector` CPU tier, SGLang HiCache host pool, llama.cpp `--no-kv-offload`, Ollama runner `--no-kv-offload`).
- **KV → NVMe/disk (the big question):** **native only in vLLM and SGLang.** vLLM `TieringOffloadingSpec` `fs` secondary tier and SGLang `--hicache-storage-backend file` write KV blocks to a filesystem directory (i.e. NVMe). **llama.cpp and Ollama have NO native NVMe KV offload** — their only disk lever is `mmap` of *weights*.
- **KV compression (orthogonal to offload):** fp8 (vLLM) / q8_0·q4_0 (llama.cpp, Ollama) / quant KV shrinks the thing you'd otherwise offload; do this *first*, offload *second*.
- **Expert streaming (MoE):** only **Colibri** does disk-native expert streaming; it is the reference "everything-is-a-memory-tier" design and is already vendored at `CortexOS/AirGPT/third_party/colibri/`.

The MVP OOM-guard orchestrates these existing knobs; it does not write kernels. See [What MVP tiering can orchestrate](#what-the-mvp-tiering-policy-can-orchestrate) and [Gaps](#gaps).

---

## Flag matrix

Legend for "what it offloads": **W** = model weights · **KV** = attention key/value cache · **EXP** = MoE experts · **—** = not offload (compression/budget/limit knob).

### vLLM

| Flag / config | What it offloads | Limit / notes | Source |
|---|---|---|---|
| `--cpu-offload-gb N` | **W** (+ can spill KV) → CPU RAM | Default `0`. "Virtual" GPU-size increase: 24 GB GPU + `10` ≈ 34 GB. Part of model streamed CPU→GPU **every forward pass** → needs fast PCIe; big throughput hit. Explicitly a *fallback to run a model that won't otherwise fit*, not a throughput optimization. Pins host RAM. | [engine args](https://docs.vllm.ai/en/latest/serving/engine_args.html), [PR #6496](https://github.com/vllm-project/vllm/pull/6496) |
| `--swap-space N` (GiB) | **KV** → CPU RAM | Size of CPU swap space **per GPU**, default `4`. Classic role: buffer for preempted/recomputed requests (beam search). In V1 repurposed to size the CPU KV block pool for swap-out-on-eviction / swap-in-on-hit. | [engine args](https://docs.vllm.ai/en/latest/serving/engine_args.html), [PR #13377](https://github.com/vllm-project/vllm/pull/13377), [RFC #16144](https://github.com/vllm-project/vllm/issues/16144) |
| `--gpu-memory-utilization F` | — (budget) | Fraction 0–1 of GPU mem for the executor (weights + activations + active KV), default `0.9`. Lower it to leave VRAM for the OS/other apps; it does **not** offload, it caps. On 12 GB, `0.85–0.9` typical. | [engine args](https://docs.vllm.ai/en/latest/serving/engine_args.html) |
| `--kv-cache-dtype fp8` (`auto`/`fp8`/`fp8_e4m3`/`fp8_e5m2`) | — (KV **compression**) | Halves KV footprint vs f16 → the cheapest way to fit more context before you need to offload at all. CUDA 11.8+. Not offload. | [engine args](https://docs.vllm.ai/en/latest/serving/engine_args.html) |
| `--max-model-len N` | — (limit) | Caps context length ⇒ caps per-sequence KV size. Lower it to stop KV from exceeding the budget. Not offload. | [engine args](https://docs.vllm.ai/en/latest/serving/engine_args.html) |
| `--kv-transfer-config '{"kv_connector":"OffloadingConnector", "kv_role":"kv_both", "kv_connector_extra_config":{...}}'` | **KV** → CPU RAM (and **NVMe/disk**, **S3**, **P2P**) | The real KV-offload path. Extends the prefix cache: completed KV blocks copied to slower/larger tiers via async DMA (`cudaMemcpyAsync`). `CPUOffloadingSpec` (default) = single CPU tier; `TieringOffloadingSpec` = CPU primary **+ secondary tiers**. `cpu_bytes_to_use` (required) sizes the CPU tier (total across workers). `offload_prompt_only` default `true` (prefill blocks only). CUDA/ROCm/XPU only. | [KV offloading guide](https://docs.vllm.ai/en/latest/features/kv_offloading_usage/) |
| ↳ secondary tier `{"type":"fs","root_dir":"/mnt/kv","n_read_threads":32,"n_write_threads":16}` | **KV** → **NVMe/filesystem** | **This is native NVMe KV offload.** Writes KV blocks as `.bin` files sharded by hash prefix under `root_dir`. Shareable across instances/runs (set `PYTHONHASHSEED=0`). Also `type:"obj"` (S3) and `type:"p2p"` (RDMA). | [KV offloading guide](https://docs.vllm.ai/en/latest/features/kv_offloading_usage/) |
| `--enable-prefix-caching` / `--no-enable-prefix-caching` | — (GPU-side KV reuse) | Automatic prefix cache; the offloading connector *extends* it. On by default in recent vLLM. | [engine args](https://docs.vllm.ai/en/latest/serving/engine_args.html) |

> **UNVERIFIED:** a couple of secondary sources mention flat `--kv-offloading-size` / `--kv-offloading-backend {native,lmcache}` flags. I could **not** confirm these against the current official docs (the confirmed API is `--kv-transfer-config` + `OffloadingConnector`). Treat `--kv-offloading-*` as version-specific/unconfirmed until checked against your pinned vLLM version's `vllm serve --help`.

### SGLang

| Flag / env | What it offloads | Limit / notes | Source |
|---|---|---|---|
| `--mem-fraction-static F` | — (budget) | Fraction of GPU mem for static allocation (**weights + KV pool**). Default ≈ `(GPU mem − reserved)/GPU mem`, falls back to `0.88`. **Lower it (0.8/0.7) as the primary OOM fix.** Not offload; it's the budget dial. | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments), [hyperparameter tuning](https://docs.sglang.io/docs/advanced_features/hyperparameter_tuning) |
| `--chunked-prefill-size N` | — (prefill activation cap) | Max tokens per prefill chunk; `-1` disables chunking. Lower to `4096`/`2048` to fix **prefill** OOM on long prompts (costs prefill speed). Not offload. | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments) |
| `--max-running-requests N` | — (concurrency cap) | Lower to fix **decode** OOM. Not offload. | [FAQ](https://docs.sglang.ai/references/faq.html) |
| RadixAttention prefix cache (**on by default**) | — (GPU-side KV reuse) | Auto KV reuse across requests sharing a prefix. `--disable-radix-cache` turns it off (also needed for deterministic output). `--radix-eviction-policy {lru,lfu,slru,priority}` (default `lru`). `--schedule-policy lpm` maximizes prefix hits. | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments), [prefix caching](https://docs.sglang.io/docs/concepts/prefix-caching) |
| `--enable-hierarchical-cache` (HiCache) | **KV** → CPU RAM (**+ NVMe/disk/remote**) | Extends KV cache into a host-memory pool and, with a storage backend, onto disk. This is SGLang's CPU/disk KV offload. | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments) |
| ↳ `--hicache-ratio F` (default `2.0`) | **KV** → CPU RAM | Host KV pool size as a multiple of the device pool. | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments) |
| ↳ `--hicache-size N` (GB) | **KV** → CPU RAM | Absolute host KV pool size; overrides `--hicache-ratio`. Default `0`. | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments) |
| ↳ `--hicache-write-policy {write_through,write_back,write_through_selective}` | **KV** tiering policy | Default `write_through`. | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments) |
| ↳ `--hicache-io-backend {kernel,direct,kernel_ascend}` | **KV** CPU↔GPU transfer | IO backend for host↔device KV movement. Default `kernel`. | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments) |
| ↳ `--hicache-storage-backend {file,mooncake,hf3fs,nixl,aibrix,...}` | **KV** → **NVMe/disk / remote** | **This is native disk/NVMe KV offload.** `file` = local filesystem (NVMe). Others are distributed/remote KV stores. Default `None` (host-RAM only). `--hicache-storage-prefetch-policy {timeout,best_effort,wait_complete}`. | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments) |
| `--cpu-offload-gb N` | **W** → CPU RAM | GBs of RAM reserved for CPU **weight** offloading. Default `0`. Same intent as vLLM's flag. | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments) |
| `--offload-mode MODE` (default `cpu`), `--offload-group-size`, `--offload-num-in-group`, `--offload-prefetch-step` | **W** → CPU RAM (layer-group pipelined) | Layer-group weight offload with prefetch — SGLang's built-in analogue of the "hold active layer, prefetch N+1" pipeline (Section B2). | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments) |
| `--enable-lmcache` + `--lmcache-config-file` | **KV** → CPU/**disk**/remote (via LMCache) | Alternative hierarchical KV solution (LMCache). | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments) |
| `--disaggregation-decode-enable-offload-kvcache` | **KV** async offload (PD decode) | Only for prefill/decode-disaggregated deployments. | [server args](https://docs.sglang.io/docs/advanced_features/server_arguments) |

### llama.cpp

| Flag / env | What it offloads | Limit / notes | Source |
|---|---|---|---|
| `-ngl, --n-gpu-layers, --gpu-layers N` (`auto`/`all`/int) | **W** (partial) → GPU; remainder stays in **CPU RAM** | The core hybrid-offload knob. `all`/`999` = everything possible on GPU; a number keeps the rest on CPU (much slower). env `LLAMA_ARG_N_GPU_LAYERS`. | [cli README](https://github.com/ggml-org/llama.cpp/blob/master/tools/cli/README.md), [multi-gpu docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/multi-gpu.md) |
| `-kvo/--kv-offload`, `-nkvo/--no-kv-offload` (default **on**) | **KV** → GPU (default) / **CPU RAM** (`--no-kv-offload`) | Default keeps KV on GPU. `--no-kv-offload` keeps the KV cache in **system RAM** while layers stay on GPU — the fix for "model fits VRAM but KV pushes it over." env `LLAMA_ARG_KV_OFFLOAD`. **CPU RAM only — no disk path.** | [cli README](https://github.com/ggml-org/llama.cpp/blob/master/tools/cli/README.md) |
| `-ctk/--cache-type-k TYPE`, `-ctv/--cache-type-v TYPE` (`f32,f16,bf16,q8_0,q4_0,q4_1,iq4_nl,q5_0,q5_1`; default `f16`) | — (KV **compression**) | Quantize KV to shrink it before offloading. `q8_0` ≈ −50% KV, negligible loss; `q4_0` more aggressive. **Requires flash attention** (`-fa on`); K tolerates quant better than V. env `LLAMA_ARG_CACHE_TYPE_K/V`. | [cli README](https://github.com/ggml-org/llama.cpp/blob/master/tools/cli/README.md), [multi-gpu docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/multi-gpu.md) |
| `--mmap` / `--no-mmap` (default **mmap on**) | **W** ↔ **disk** (page cache) | mmap maps the GGUF; weights page in from disk on demand and the OS page cache holds them (so weights can exceed RAM, served lazily from NVMe). `--no-mmap` loads fully into RAM (faster steady-state, higher RSS, no disk paging). env `LLAMA_ARG_MMAP`. This is llama.cpp's *only* disk-backed lever — and it is **weights, not KV**. | [cli README](https://github.com/ggml-org/llama.cpp/blob/master/tools/cli/README.md), [issue #9059](https://github.com/ggml-org/llama.cpp/issues/9059) |
| `-fa, --flash-attn {on,off,auto}` | — (enables quant KV) | Required for quantized V cache and for tensor split. | [multi-gpu docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/multi-gpu.md) |
| `-c, --ctx-size N` | — (limit) | KV size ∝ `n_ctx`; lower it to cut KV VRAM. | [multi-gpu docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/multi-gpu.md) |

### Ollama (wraps llama.cpp)

| Env / option | What it offloads | Limit / notes | Source |
|---|---|---|---|
| `num_gpu` (model option / API param — **not** an env var) | **W** (partial) → GPU; rest → **CPU RAM** | Number of layers on GPU; `-1` = auto. `num_gpu: 20` = first 20 layers on GPU, rest CPU. There is **no** `OLLAMA_GPU_LAYERS` env. | [FAQ](https://docs.ollama.com/faq), [tuning guide](https://eastondev.com/blog/en/posts/ai/20260410-ollama-performance-optimization/) |
| `OLLAMA_FLASH_ATTENTION=1` | — (enables quant KV) | Off by default; **must be on or `OLLAMA_KV_CACHE_TYPE` is ignored.** | [FAQ](https://docs.ollama.com/faq) |
| `OLLAMA_KV_CACHE_TYPE` (`f16`/`q8_0`/`q4_0`; default `f16`) | — (KV **compression**) | `q8_0` ≈ −50% KV (negligible loss, recommended), `q4_0` ≈ −75% (small-medium loss). **Global** (all models). Needs flash attention. | [FAQ](https://docs.ollama.com/faq) |
| `OLLAMA_GPU_OVERHEAD` (bytes) | — (budget) | Reserve VRAM for the system/other apps. Default `0`. | [env var reference](https://modelpiper.com/blog/ollama-environment-variables) |
| runner flag `--no-kv-offload` | **KV** → **CPU RAM** | Inherited from llama.cpp; keeps KV in system RAM while layers stay on GPU. Runner behavior, **not** an env var — there is **no** `OLLAMA_KV_OFFLOAD`. Open discussion [ollama#9750](https://github.com/ollama/ollama/issues/9750) on preferring to spill layers over KV. | [env var reference](https://modelpiper.com/blog/ollama-environment-variables) |

> Ollama has **no NVMe KV offload** and no dedicated env var for KV offload; it offloads layers+KV to GPU together and otherwise falls back to CPU RAM.

### Colibri (vendored disk-MoE runtime)

Source: vendored `CortexOS/AirGPT/third_party/colibri/README.md`, `c/resource_plan.py`, `c/tier.h`. Colibri is not flag-compatible with the others — it is a **whole-hierarchy MoE engine** whose entire premise is streaming experts from disk. Reference design for our tiering policy.

| Knob (env or flag) | What it offloads / does | Limit / notes | Source |
|---|---|---|---|
| Architecture (implicit) | **EXP** → **disk (NVMe)**; dense stays in **RAM**; hot experts pinned in **VRAM** | Dense part (attention, shared experts, embeddings, ~17B params @ int4 ≈ **9.9 GB**) resident in RAM. ~21,504 routed experts (~19 MB each @ int4, ~**370 GB**) live on disk, streamed on demand with a **per-layer LRU cache** + optional pinned hot-store + OS page cache as free L2. | README |
| `--ram N` / `RAM_GB` | RAM budget for the warm expert cache | Expert cache **auto-sizes from `MemAvailable`** at startup (honest peak projection so the OOM-killer never fires); auto-*raises* the LRU cap to fill the budget since 2026-07-10. Needs ≥16 GB RAM min. | README, `resource_plan.py` |
| `COLI_CUDA=1`, `COLI_GPU`/`COLI_GPUS`, `CUDA_EXPERT_GB N` | pin hottest **EXP** in **VRAM** | VRAM hot tier for *resident* experts. **Streaming experts deliberately stay on the CPU path** — copying an expert NVMe→GPU per use just trades the disk bottleneck for a PCIe one. VRAM tier earns its keep only when the CPU matmul is the weak link. Ada/40-series = `CUDA_ARCH=sm_89`; Windows needs a runtime `coli_cuda.dll`. | README |
| `PIN=stats.txt PIN_GB=N` | promote measured-hot **EXP** into pinned RAM/VRAM | Two-phase: record routing frequencies (`STATS=stats.txt`), then pin the hottest set. "Learning cache": `.coli_usage` histogram next to the model auto-pins hot experts at startup — gets faster with use. | README |
| `--repin N` / `REPIN` (LFRU) | live hot/cold **EXP** swap | At safe turn boundaries an LFRU score (frequency primary, recency tiebreak) replaces ≤4 pinned experts; 25% hysteresis prevents thrash (`tier.h::tier_pick_lfru`). `--policy balanced` sets `REPIN=64`; `--policy quality` leaves it off. | README, `tier.h` |
| `--topp 0.7` | adaptive expert top-p | Routes to fewer experts ⇒ 30–40% less disk traffic; measured clean ~1.6× end-to-end on a small-RAM box. Quality-lossy override (prints a warning). | README |
| `--auto-tier`, `coli plan`, `coli doctor` | plan/apply the disk/RAM/VRAM placement | `plan` reads only safetensors headers, reports dense/expert footprint, safe RAM cache cap, bounded VRAM hot tier, and the expected bottleneck as versioned JSON. `doctor` = read-only runnability check. | README, `resource_plan.py` |
| `IO_THREADS N`, `PILOT=1`, `DIRECT=1` | disk pipeline tuning | Deferred cold-read pipeline (default 8 loader threads); `PILOT` = router-lookahead prefetch (next layer's routing 71.6% predictable); `DIRECT` = O_DIRECT (Linux/measured NVMe). | README |
| `DRAFT=n` (MTP), `GRAMMAR=g.gbnf`, `KVSAVE`, `CAP_RAISE`, `AUTOPIN` | speculative decode / KV persistence / cache caps | MTP int8 head ⇒ 2.2–2.8 tok/forward warm; `.coli_kv` persists compressed MLA KV across restarts (~182 KB/token) so conversations reopen warm with zero re-prefill (`KVSAVE=0` disables). | README |

**Colibri on our box (RTX 4070 12 GB / 32 GB RAM / Windows 11):** *feasible but slow, and RAM-bound.* At 32 GB RAM the expert cache auto-caps to ~2 slots/layer, so decode stays mostly cold. A measured native-Windows/32 GB datapoint (Intel i5-12600K, no WSL, [issue #113](https://github.com/JustVugg/colibri/issues/113)) got **0.08 tok/s** cold (hit 3.7%); another 32 GB Windows box ([#128](https://github.com/JustVugg/colibri/issues/128)) reached **~0.5 tok/s warm** after ~7-prompt warmup. It needs the ~370 GB int4 GLM-5.2 model on a **local NVMe** (NTFS ok, never a network/9p mount). Verdict for G3: real toggle only for "patient/background" use on this class of box — the RAM cap, not the disk, is the binding constraint here.

---

## What the MVP tiering policy can orchestrate

The OOM-guard MVP does **not** need to write kernels — it selects a backend and sets these knobs in a fixed escalation order. Proposed `netie.engine.tiering` policy ladder (least → most disruptive to latency/quality):

1. **Shrink KV first (free, near-lossless):** `--kv-cache-dtype fp8` (vLLM) · `OLLAMA_KV_CACHE_TYPE=q8_0` + `OLLAMA_FLASH_ATTENTION=1` (Ollama) · `-ctk q8_0 -ctv q8_0 -fa on` (llama.cpp). ~50% KV reduction before any offload.
2. **Cap the working set:** lower `--max-model-len` (vLLM) / `--ctx-size` (llama.cpp) / `num_ctx` (Ollama); lower `--max-running-requests` / `--chunked-prefill-size` (SGLang); dial `--gpu-memory-utilization` / `--mem-fraction-static` to leave VRAM headroom.
3. **Offload KV → CPU RAM:** vLLM `OffloadingConnector` `CPUOffloadingSpec` (`cpu_bytes_to_use`) or `--swap-space` · SGLang `--enable-hierarchical-cache --hicache-size N` · llama.cpp/Ollama `--no-kv-offload`.
4. **Offload KV → NVMe (only vLLM + SGLang):** vLLM `TieringOffloadingSpec` + `{"type":"fs","root_dir":<nvme>}` · SGLang `--hicache-storage-backend file`. **This is where our own NVMe KV page-cache tier plugs in / competes.**
5. **Offload weights → CPU RAM (run-bigger-than-VRAM):** vLLM `--cpu-offload-gb` · SGLang `--cpu-offload-gb`/`--offload-mode cpu` (+ group/prefetch) · llama.cpp `-ngl <partial>` · Ollama `num_gpu <partial>`.
6. **MoE-only, disk-native:** hand off to Colibri (`--ram`, `PIN`/`PIN_GB`, `--auto-tier`, `--topp`) for models whose experts must stream from disk.

Mapping to the brief's design: steps 1–2 = "shrink before spill"; step 3–4 = the aiDAPTIV-style "swap cold KV to slower tier" behavior we mimic; step 5 = HuggingFace/DeepSpeed-style layer offload but backend-native; SGLang `--offload-group-size`/`--offload-prefetch-step` is the closest built-in match to the "hold active layer, prefetch N+1, evict N−1" pipeline (Section B2). Colibri is the built-from-scratch reference for step 6 and for the predictive-prefetch idea (Section B3: `PILOT` router-lookahead, learned `.coli_usage` cache).

---

## Gaps

- **Native NVMe KV offload: YES for vLLM & SGLang, NO for llama.cpp/Ollama.**
  - vLLM `TieringOffloadingSpec` (`fs` tier) and SGLang `--hicache-storage-backend file` write KV blocks to a local directory (NVMe) natively, both with async prefetch and eviction policies. This directly overlaps the plan's "our NVMe KV page cache" — decide whether the MVP *uses* the backend's native tier or *replaces* it with `netie.brain` (recommendation: use native for vLLM/SGLang, build our own only for the llama.cpp/Ollama path where none exists).
  - llama.cpp/Ollama: **CPU RAM is the only KV offload target.** Their disk lever (`mmap`) is for **weights** only. If we want NVMe KV on the personal (Ollama) path, we must build it ourselves above the runner — the backend won't do it.
- **`--cpu-offload-gb` is a fallback, not an optimizer** (vLLM & SGLang): it re-streams weights across PCIe every forward pass → throughput collapses. Use only to make a model *run*, and bench TTFT against the gate (Section A3 numbers needed).
- **KV vs weight offload interaction:** when both won't fit, spilling *layers* to CPU is usually faster than spilling *KV* (Ollama [#9750]). Our policy should prefer KV-compress → KV-offload → layer-offload in that order, but this ordering is workload-dependent and needs the A3 PCIe/NVMe bandwidth numbers to set thresholds.
- **UNVERIFIED flags to confirm against pinned versions:** vLLM flat `--kv-offloading-size`/`--kv-offloading-backend` (couldn't confirm; the confirmed API is `--kv-transfer-config`). Always re-check with `vllm serve --help` / `python -m sglang.launch_server --help` for the exact version we vendor, since offload APIs are moving fast (vLLM V1 KV offload is actively landing per RFC #16144).
- **Windows reality (G1/G2):** vLLM/SGLang have no native Windows build — they run via Docker + WSL2 GPU passthrough on this box (confirm separately in G1). llama.cpp (native CUDA) and Ollama (native) are the zero-Docker personal paths. Colibri builds native Windows 11 (MinGW-w64), GPU tier via runtime `coli_cuda.dll` (`sm_89` for the 4070).

---

## Links (verified sources)

- vLLM engine args: https://docs.vllm.ai/en/latest/serving/engine_args.html
- vLLM KV offloading usage guide: https://docs.vllm.ai/en/latest/features/kv_offloading_usage/
- vLLM CPU offload PR #6496: https://github.com/vllm-project/vllm/pull/6496
- vLLM V1 KV→CPU PR #13377: https://github.com/vllm-project/vllm/pull/13377
- vLLM KV offload RFC #16144: https://github.com/vllm-project/vllm/issues/16144
- SGLang server arguments: https://docs.sglang.io/docs/advanced_features/server_arguments
- SGLang hyperparameter tuning: https://docs.sglang.io/docs/advanced_features/hyperparameter_tuning
- SGLang FAQ (OOM): https://docs.sglang.ai/references/faq.html
- SGLang prefix caching: https://docs.sglang.io/docs/concepts/prefix-caching
- llama.cpp CLI README (flag table): https://github.com/ggml-org/llama.cpp/blob/master/tools/cli/README.md
- llama.cpp multi-gpu docs: https://github.com/ggml-org/llama.cpp/blob/master/docs/multi-gpu.md
- llama.cpp --no-mmap issue #9059: https://github.com/ggerganov/llama.cpp/issues/9059
- Ollama FAQ (Flash Attention, KV cache type): https://docs.ollama.com/faq
- Ollama env var reference: https://modelpiper.com/blog/ollama-environment-variables
- Ollama KV offload discussion #9750: https://github.com/ollama/ollama/issues/9750
- Colibri: vendored `CortexOS/AirGPT/third_party/colibri/` (README, `c/resource_plan.py`, `c/tier.h`); upstream https://github.com/JustVugg/colibri

---
*B4 finding · dated 2026-07-18 · unblocks OOM-guard MVP. Feeds the `netie.engine.tiering` policy table (steps 1–6 above).*
