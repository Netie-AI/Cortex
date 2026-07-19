# NETIE ENGINE UP — Research & Resource Brief
**For user-executed research gathering. Fable 5 / Claude build against the findings.**
Companion to [`../strategy/NETIE_ENGINE_UP_PLAN.md`](../strategy/NETIE_ENGINE_UP_PLAN.md). Authored 2026-07-18.

---

## Decisions locked (from user, 2026-07-18) — supersedes plan §9 Q1–3
1. **Track order:** Registry-first, then interleave. Build `netie.engine.registry` (E0) + `netie.brain.store` interface (M0) **before** any backend/store, so everything plugs into stable interfaces.
2. **v1 target:** Personal **and** business in lockstep. Every phase ships a personal-laptop variant and a business/collaborative variant together.
3. **OOM-guard depth:** **MVP orchestration** for v1 (drive existing offload knobs + our NVMe KV/vector page cache + predictive prefetch). Deep aiDAPTIV-style interception is a **research track** (Section B) surfaced here for the user to gather before we decide to escalate.
4. **Collaboration target:** live repo is `C:\Users\user\RUMA\Cortex` (branch `dms-v2`, same repo as `F:\Cortex`). Cortex-first; AirGPT is UI/executor.

---

## How to use this brief
Each item is a **research task** with: **Why** (which build decision it unblocks) · **Find** (the specific answer we need) · **Sources** (repos/papers/queries to start from) · **Back to me** (the artifact/number that changes the code).
Priorities: **P0** = blocks MVP build · **P1** = needed for phase defaults · **P2** = optimization / advanced toggles.
Return findings as short markdown notes into `docs/research/findings/<section>.md` (bullet answers + links + any numbers). I fold each into the phase specs and flip its "research-gated" checkbox.

**Hardware context to include in every "tell me your setup" prompt:** RTX 4070 (12 GB VRAM), i5-13490F (16 threads), 32 GB RAM, ~200 GB free C: + multi-TB NVMe/HDD, Windows 11 + Docker Desktop + WSL2, Python 3.10. Personal target = this class of laptop. Business target = same laptop-as-host **or** $5 VPS / client NAS.

---

## Section A — System-level foundations *(the flagged "don't understand yet" gap)* — **P0**

### A1. Virtual address space & memory mapping (`mmap`) — P0
- **Why:** The entire "serialize embeddings to raw `.bin`, mmap the chunk, evict hot vectors to free RAM" design (plan §5c) and the OOM-guard depend on understanding mmap. This is the foundation the user explicitly flagged.
- **Find:** How `mmap` maps a file into virtual address space; lazy page-in on access; how the OS page cache holds mapped pages; the difference between *virtual* reservation and *resident* (physical) memory; when a mapped read hits disk vs cache; how to read a specific row by byte offset without loading the whole file.
- **Sources:** `man mmap` / `man madvise`; Python `mmap` module docs; "mmap tutorial" + "page cache explained"; Qdrant blog "Memory consumption / mmap"; Google "scaled/‌distributed memory mapping" (the user's reference — find the actual paper/talk, likely related to ScaNN / TensorStore / file-system-level vector serving).
- **Back to me:** A short primer + answers to: (a) can we `mmap` a growing append-only `vectors.bin` safely? (b) how to force-evict pages (`madvise(MADV_DONTNEED)`) to free RAM after a query? (c) Windows equivalent of madvise (`PrefetchVirtualMemory`, `VirtualUnlock`, memory-mapped `CreateFileMapping`).

### A2. OS page cache, eviction, `madvise`, huge pages — P0
- **Why:** "Evict the vector from hot memory to free RAM" (user) = controlling page-cache residency. Determines how our raw-mmap KNN tier keeps idle RAM ~0.
- **Find:** `MADV_WILLNEED` (prefetch) vs `MADV_DONTNEED`/`MADV_COLD` (evict); how huge pages (2 MB) affect large contiguous vector arrays; how residency is measured (`mincore`). Windows: `PrefetchVirtualMemory`, working-set trimming, `SetProcessWorkingSetSize`.
- **Sources:** `man madvise`, `man mincore`; Linux MM docs; Windows memory-management API docs.
- **Back to me:** The concrete syscalls/APIs we'll call (Linux + Windows) to prefetch and evict — this becomes `netie.brain.stores.rawknn` residency control.

### A3. PCIe / NVMe transfer characteristics for offload — P1
- **Why:** OOM-guard MVP offloads cold KV/experts to NVMe; feasibility hinges on PCIe/NVMe bandwidth vs recompute cost (the aiDAPTIV "swap vs recompute" trade).
- **Find:** Real GB/s for PCIe 4.0 x16 host↔GPU and NVMe Gen4 read; latency of a 4 KB vs 2 MB transfer; async copy engines / CUDA streams for overlapping compute + transfer.
- **Sources:** NVIDIA CUDA streams/`cudaMemcpyAsync` docs; NVMe Gen4 benchmarks; the Phison aiDAPTIV "asynchronous transfers across PCIe" claims.
- **Back to me:** Bandwidth/latency numbers → sets the max offloadable KV size before TTFT regresses past our bench gate.

---

## Section B — OOM-guard / predictive tiering (MEXT / Phison aiDAPTIVLink) — **P0 (MVP) + P2 (deep)**

### B1. aiDAPTIVLink interception mechanism (what's actually public) — P1
- **Why:** Decide whether deep interception (catch OOM below PyTorch) is buildable by us or stays a "buy/partner" item; MVP mimics the *behavior* via backend knobs.
- **Find:** How aiDAPTIVLink hooks PyTorch allocations; is there an SDK/API; is it AMD/Instinct-only post-acquisition; what's open vs proprietary.
- **Sources (user-provided, verify + extract):** Phison Enterprise aiDAPTIV pages; Tech Field Day + COMPUTEX YouTube talks; SemiWiki/RCRTech MEXT coverage; the IoT flyer PDF the user linked.
- **Back to me:** Is there a usable SDK, or is this pattern-only? → decides E4 depth.

### B2. Layer / KV slicing + pipelined offload — P1
- **Why:** The "GPU holds only active layer; N+1 prefetched, N−1 evicted" pipeline is the reusable pattern for E4.
- **Find:** Public implementations of layer-wise offload (HuggingFace `accelerate` `device_map="auto"` + `offload_folder`; DeepSpeed-Inference ZeRO-Inference; FlexGen). How they slice and schedule.
- **Sources:** `huggingface/accelerate` big-model-inference docs; `FMInference/FlexGen`; DeepSpeed ZeRO-Inference; the user's aiDAPTIV slicing notes.
- **Back to me:** Which existing offload framework we wrap for the MVP (FlexGen vs accelerate vs backend-native) + their KV-offload limits.

### B3. Predictive prefetch model — P2
- **Why:** MEXT's edge = a transformer predicting which cold pages are needed next. Our MVP starts with a heuristic; this decides if/when to add a learned predictor.
- **Find:** What signal MEXT uses (attention scores? access recency?); simple learned cache-replacement (LeCaR, learned-LRU) results; whether a tiny model beats ARC/LRU for KV pages.
- **Sources:** MEXT/X·MikeLongTerm threads; "learned cache replacement" papers (LeCaR, Parrot); prefix-cache reuse in SGLang RadixAttention.
- **Back to me:** Heuristic vs learned recommendation + the metric (prefetch hit-rate) we bench in `bench/tiering.py`.

### B4. Backend-native offload knobs (the MVP substrate) — **P0**
- **Why:** MVP OOM-guard = orchestrate these, not write kernels. Need exact flags + limits per backend.
- **Find (exact flags + what they offload):**
  - vLLM: `--cpu-offload-gb`, `--swap-space`, `--gpu-memory-utilization`, `--kv-cache-dtype fp8`, `--max-model-len`; does it offload KV to CPU/NVMe or only weights?
  - SGLang: `--mem-fraction-static`, chunked prefill, RadixAttention prefix cache reuse, offload options.
  - llama.cpp / Ollama: `--n-gpu-layers` (partial offload), `--cache-type-k/v` (TurboQuant flags per plan), `--no-mmap`/`--mmap`, `OLLAMA_KV_CACHE_TYPE`.
  - Colibri: expert-streaming config, RAM/NVMe requirements, how it decides hot vs cold experts.
- **Sources:** vLLM docs (engine args), SGLang server args docs, `ggml-org/llama.cpp` server README + discussion #20969 (TurboQuant), `third_party/colibri` README.
- **Back to me:** A flag matrix (backend × what-it-offloads × limit) → directly becomes the `netie.engine.tiering` policy table.

---

## Section C — KV-cache & quant methods — **P1 (with P0 defaults)**

### C1. TurboQuant (proposed default KV mode) — P1
- **Why:** Plan sets TurboQuant as auto GPU KV default; need integration surface + maturity.
- **Find:** Is it merged in llama.cpp mainline or a fork (`--cache-type-k/v turbo3`)? vLLM support? Accuracy (Needle 0.997) reproduced? License.
- **Sources (user-provided):** turbo-quant.com; deepinfra blog; llama.cpp discussion #20969; digitalapplied/medium explainers.
- **Back to me:** Ready-to-use? which backend? → sets E2 default.

### C2. KIVI / QTIP / SnapKV / RotorQuant / IsoQuant / PlanarQuant (advanced toggles) — P2
- **Why:** These become opt-in toggles behind a "research" flag, promoted to default only after a passing bench.
- **Find (per method):** repo + backend it runs in + reported Needle/RULER score + compression ratio + speedup + hardware tested.
  - KIVI (2-bit per-channel/token, Needle 0.981) · QTIP (rotation+distortion, ExLlamaV3) · SnapKV (token eviction, 3.6× faster, 0.858) · RotorQuant (Clifford rotors, 10–19× vs cuBLAS) · IsoQuant/PlanarQuant (5×–80× KV).
- **Sources (user-provided):** github topics/turboquant; the RotorQuant reddit + repo; ExLlamaV3; boringbot substack.
- **Back to me:** A comparison table (method × backend × recall × speedup × VRAM) → `docs/research/findings/kv_methods.md`, feeds `bench/needle.py` and the toggle registry.

### C3. Weight quant AWQ / GPTQ availability for target models — P1
- **Why:** Plan defaults GPU serve to AWQ 4-bit; need to confirm quantized checkpoints exist for our target models.
- **Find:** AWQ/GPTQ checkpoints on HF for Qwen3-8B, Qwen2.5-7B/14B, Llama-3.1-8B, DeepSeek-R1-distill; vLLM/SGLang load support; VRAM footprint at 12 GB.
- **Sources:** HuggingFace model search (`*-AWQ`, `*-GPTQ`); vLLM supported-quant docs.
- **Back to me:** Confirmed model→quant→VRAM list → the "supported by my device" model marketplace facet.

### C4. Bench methodology — P1
- **Why:** Every KV/quant toggle needs an objective gate before becoming default.
- **Find:** Standard long-context eval (Needle-in-a-Haystack, RULER, LongBench) — datasets + scoring scripts we can run locally.
- **Sources:** `gkamradt/LLMTest_NeedleInAHaystack`; NVIDIA `RULER`; LongBench.
- **Back to me:** The exact eval script we vendor into `bench/needle.py`.

---

## Section D — Vector / memory stores — **P0**

### D1. sqlite-vec (proposed personal default) — P0
- **Why:** Default personal store (0 idle RAM, $0, file-on-disk). Need limits before committing.
- **Find:** Max practical vectors before query slows; does it do ANN or brute-force; int8/binary quant support; Windows build/loadable-extension availability; concurrent read/write with the app process.
- **Sources:** `asg017/sqlite-vec` repo + docs; sqlite-vec benchmarks.
- **Back to me:** Practical vector ceiling + whether it's brute-force (informs the D5 crossover) → sets M1.

### D2. Qdrant with mmap + quantization — P0
- **Why:** Business/scale store; the "$5 VPS, low RAM, vectors on disk" path.
- **Find:** `on_disk` + mmap config; scalar/binary quantization config + recall impact; RAM per 1M vectors on-disk vs in-RAM; embedded (in-process) vs server mode.
- **Sources:** Qdrant docs (storage, quantization, optimizer); Qdrant "minimal RAM" blog.
- **Back to me:** Config recipe for low-RAM on-disk mode → `netie.brain.stores.qdrant`.

### D3. pgvector (HNSW vs IVFFlat) — P1
- **Why:** Business relational + vector; reuses Cortex's Postgres semantic tier.
- **Find:** HNSW vs IVFFlat RAM/latency; `halfvec`/int8 support; `1.3 GB storage` claim (user) — verify per-vector cost; RLS compatibility (business dual-brain).
- **Sources:** `pgvector/pgvector` README; Supabase pgvector guides.
- **Back to me:** RAM/latency profile + RLS confirmation → M4/M6.

### D4. MongoDB Atlas Vector Search — P2
- **Why:** NoSQL/MERN metadata path (user listed).
- **Find:** Free-tier vector limits; index build; whether self-hostable (Atlas-only vs Community); JSON metadata filtering.
- **Sources:** MongoDB Atlas Vector Search docs.
- **Back to me:** Is it viable self-hosted or Atlas-cloud-only? → decides if it's a real toggle or cloud-optional.

### D5. Brute-force KNN (GPU/CPU) + crossover point — **P0**
- **Why:** User: "brute force at 10,000 vectors within a second, faster than loading a heavy index graph." Need the real crossover where brute-force beats HNSW.
- **Find:** Vectorized dot/cosine on CPU (numpy) vs GPU (torch/cupy) vs `faiss` IndexFlat; measured latency at 1k/10k/100k/1M × dim 256/768/1536; the N where HNSW build+load cost wins.
- **Sources:** faiss wiki (Flat vs HNSW); numpy/torch batched cosine benchmarks.
- **Back to me:** A crossover table → the auto-selection rule in `netie.brain.store` (brute-force below N, index above).

### D6. Raw-mmap embedding layout + chunk store (SQLite vs RocksDB) — P0
- **Why:** The user's explicit design: serialized raw `.bin` + metadata sidecar + chunk DB. Need the file format + which KV store for chunks.
- **Find:** Fixed-width row layout for float32/int8 vectors (offset = id × dim × bytes); SQLite vs RocksDB for `id→text/meta` (write amplification, idle RAM, Windows support); append-only + compaction.
- **Sources:** RocksDB vs SQLite comparisons; A1/A2 mmap findings.
- **Back to me:** Final on-disk layout spec → `netie.brain.stores.rawknn` file format.

---

## Section E — Embedding compression — **P1**

### E1. Scalar quantization (float32→int8) — P1
- **Why:** −75% footprint, ~98% recall (user). Confirm on our embedder + stores.
- **Find:** Recall@10 delta at int8 vs float32 on a fixed eval; native support in sqlite-vec / Qdrant / pgvector.
- **Sources:** Qdrant quantization benchmarks; MTEB quantization notes.
- **Back to me:** Recall delta number → default-on or opt-in decision for `brain.compress=sq`.

### E2. Matryoshka truncation (MRL) — P1
- **Why:** 1536→256 dim shrinks index proportionally (user).
- **Find:** Which embedders support MRL (Nomic-embed, `text-embedding-3-*`, BGE?); recall vs dim curve (768→512→256→128); does Cortex's current BGE-M3 (`nlp/embedder_bge.py`) support truncation?
- **Sources:** Matryoshka Representation Learning paper; Nomic embed docs; MTEB.
- **Back to me:** Embedder choice + safe truncation dim → `brain.compress.mrl` default.

### E3. Embedder selection — P1
- **Why:** One embedder must serve both planes; Cortex already uses BGE-M3.
- **Find:** BGE-M3 vs Nomic-embed-text-v1.5 vs bge-small on: MTEB score, dim, MRL support, local CPU/GPU speed on our box, license.
- **Back to me:** Confirm/replace `nlp/embedder_bge.py` default.

---

## Section F — Dual-brain / persistent memory patterns — **P1**

### F1. mem0 architecture + API — P1
- **Why:** User wants mem0 as a selectable memory layer; AirGPT docs claim "mem0-compatible shape."
- **Find:** mem0's memory model (add/search/update/graph); self-hosted vs cloud; storage backends it supports; API shape to stay compatible.
- **Sources:** `mem0ai/mem0` repo + docs.
- **Back to me:** The interface we mirror in `netie.brain.mem0`.

### F2. Long-term agent memory prior art (MemGPT/Letta, MemPalace) — P2
- **Why:** Inform the always-learning + verbatim-wing design.
- **Find:** MemGPT/Letta tiered-memory + self-editing; any "memory palace"/spatial verbatim store patterns; how they decide promote/evict.
- **Sources:** `letta-ai/letta` (MemGPT); relevant papers.
- **Back to me:** Patterns to adopt for hot→warm→cold promotion + forgetting.

### F3. Always-learning ingestion + forgetting policy — P1
- **Why:** "Keep embedding forever, become company brain" needs a retention model that never silently loses learning but controls size.
- **Find:** Best practice for continuous ingest without unbounded growth (dedup, summarize-then-demote, TTL on episodic only); AirGPT's existing `thin/forget` + carry-pack as baseline.
- **Back to me:** The retention state machine → M3.

### F4. Role-labelled collaborative memory + RLS — P1
- **Why:** Business dual-brain: label contributions by assigned role/position; enforce who reads what.
- **Find:** Postgres RLS patterns for per-role vector rows (reuse CORTEX_COMPLETE_PLAN Phase 3); how to stamp role at write from authenticated identity (reuse AirGPT `access_policy` + hub roles).
- **Back to me:** The row schema + RLS policy → M4.

---

## Section G — Runtime on Windows / device — **P0**

### G1. vLLM / SGLang on Windows via Docker + WSL2 GPU — P0
- **Why:** No native Windows vLLM/SGLang; business/power path depends on Docker GPU passthrough working on this box.
- **Find:** Steps to enable NVIDIA GPU in Docker Desktop + WSL2; verify `docker run --gpus all` sees the 4070; the `vllm/vllm-openai` + `lmsysorg/sglang` run commands + VRAM footprint for an 8B AWQ at 12 GB.
- **Sources:** NVIDIA Container Toolkit + WSL2 CUDA docs; vLLM/SGLang Docker docs.
- **Back to me:** Confirmed working command + any driver/toolkit prereqs → E1 install manager.

### G2. Ollama / llama.cpp native Windows GPU — P0
- **Why:** The zero-Docker personal path.
- **Find:** Ollama native Windows CUDA usage on the 4070; llama.cpp Windows CUDA build + TurboQuant cache flags; how Ollama exposes KV-cache-type / offload env vars.
- **Sources:** Ollama Windows docs; llama.cpp Windows build.
- **Back to me:** Confirmed native path + KV flags → E1/E2 personal default.

### G3. Colibri run requirements — P1
- **Why:** Optional disk-MoE engine already vendored.
- **Find:** Exact RAM/NVMe to run a GLM-class MoE; the `coli serve` command; how it fits 12 GB VRAM + 32 GB RAM (or if it needs more).
- **Sources:** `third_party/colibri` README + `resource_plan.py`.
- **Back to me:** Feasibility on our box → whether Colibri is a real v1 toggle or "needs bigger host."

---

## Section H — Cortex DMS-V2 integration (live repo) — **P0**

### H1. Map integration points in `C:\Users\user\RUMA\Cortex` (branch dms-v2) — P0
- **Why:** `netie.engine` + `netie.brain` must plug into the *live* Cortex, not a stale copy. There is active uncommitted work (`CortexOS/api/app.py`, `sidecar_routes.py`, `scam_guard.py`).
- **Find (I will do this part — it's code-reading, not web research):** where API routes register (`CortexOS/api/app.py`), how `personality/memory.py` is wired today, how `execution/model_router.py` + `routing/adapters` are invoked, where `pyproject.toml` extras get added.
- **Back to me (from user):** confirm dms-v2 is the branch to build on, and whether to branch `netie-engine-up` off it.

---

## Priority-ordered gather list — send me the **P0 bundle** first
1. **A1 + A2** — mmap / page-cache / evict primer (Linux + Windows APIs). *Unblocks the whole raw-mmap memory tier.*
2. **B4** — backend offload flag matrix (vLLM/SGLang/llama.cpp/Colibri). *Unblocks OOM-guard MVP.*
3. **D1 + D5 + D6** — sqlite-vec limits, brute-force crossover, raw-file layout + chunk store. *Unblocks the personal memory default.*
4. **G1 + G2** — Docker/WSL2 GPU + Ollama/llama.cpp native confirmations. *Unblocks real backends.*
5. **C1 + C3** — TurboQuant readiness + AWQ/GPTQ model availability. *Unblocks GPU serve defaults.*
Then P1 (C2, D2–D3, E1–E3, F1/F3/F4, B1–B2, G3), then P2.

## What I do in parallel (no external research needed)
- **E0** — `netie.engine.registry`: capability descriptors + auto-profile from `model_probe` + `specs()` aggregator (replaces AirGPT `netie_engine.py` shim).
- **M0** — `netie.brain.store`: the upsert/query/evict protocol + conformance test (2 stub impls).
- **H1** — read the live dms-v2 integration points and write the wiring stubs.
These are the registry-first unblockers; they don't wait on findings. Everything after plugs into them as your findings land.

---
*Return findings to `docs/research/findings/`. Each P0 finding flips a build gate. Ping me the moment the P0 bundle lands and I'll start turning gates green.*
