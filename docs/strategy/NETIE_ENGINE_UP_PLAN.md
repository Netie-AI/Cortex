# NETIE ENGINE UP — Master Build & Research Plan
**Unified local inference + persistent dual-brain memory, built in Cortex first, driven by AirGPT.**
Status: PLAN (for Fable 5 to think-more + execute). Nothing here is shipped unless the inventory in §2 says so.
Companions: [`NETIE_CORTEX_MASTER_PLAN.md`](NETIE_CORTEX_MASTER_PLAN.md) (business horizons), [`../../CORTEX_COMPLETE_PLAN.md`](../../CORTEX_COMPLETE_PLAN.md) (DMS phases), [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md) (honest inventory).

---

## 0. Posture — read this first

1. **Cortex is the engine; AirGPT is the driver.** Every capability here is built as a `netie.*` module in Cortex (the `netie` package — see `pyproject.toml [tool.poetry] packages`), exposed through CortexOS API, and only *then* wired into AirGPT's UI (the Netie Engine Hosting page shipped 2026-07-18). AirGPT never owns engine logic; it executes and visualizes Cortex.
2. **One runtime, two planes.** "Netie Engine Up" = **Inference Plane** (run any model without OOM) + **Memory Plane** (persistent, always-learning dual brain). They share one selection/capability registry and one specs surface (the `(i)` popover).
3. **Two users, one stack.** *Personal* = laptop-local, hidden persistent memory, zero-config ("treat users as non-technical"). *Business* = same stack + collaboration, role-labelled access, audit, leakage monitoring. Same core; business adds governance, not a fork.
4. **Selectable, with safe defaults.** Every optimizer/store/engine is *toggleable* (`vLLM | Ollama | SGLang | Colibri | KV-quant flavor | vector store | mem0 | MemPalace …`) but ships an **auto profile** that picks the right one for the detected hardware. Idiots get auto; power users get the toggles.
5. **Honesty rule (inherited from ARCHITECTURE.md):** planned ≠ shipped. Every phase has acceptance + anti-scope + a bench gate.

---

## 1. What the user asked for (captured, nothing dropped)

- **Netie Engine "Up"** — one selectable runtime combining: vLLM + Ollama + SGLang + Colibri + Qdrant + NoSQL + Mem0 + MemPalace + KV-Cache Optimization + MoE + DeepSeek DSA + Google TurboQuant — *each individually enable/disable-able*.
- **Run large models without OOM** — via KV-cache compression, quant, paging, MoE expert streaming, and **MEXT/Phison-aiDAPTIVLink-style predictive memory tiering** (offload cold KV to NVMe, prefetch before use, intercept OOM).
- **Research + implement + test/bench/debug every memory layer** — including address-space/memory-mapping (flagged "don't understand yet — learn later"), brute-force KNN vs index graphs, serialized raw-embedding files + metadata sidecar, scaled memory-mapping (Google-style), hot→cold eviction.
- **Vector/memory store selection** — SQLite(sqlite-vec) vs Qdrant(mmap) vs MongoDB Atlas vs Postgres(pgvector), with scalar quantization + Matryoshka truncation; cost/RAM-aware; hostable on a $5 VPS *or* the client's own laptop.
- **Persistent "company dual brain"** — laptop-local, hidden, always learning, embeddings forever; business version is collaborative with role/position labels on assigned access → becomes the company's persistent skill memory.
- **Cortex-first** — improve Cortex Engine, AirGPT is the UI/executor on top.
- **Deliverable now = the full plan.** Fable 5 executes; leave goal-driven orchestration space + open questions.

---

## 2. Honest current-state inventory (the reuse map)

**Do not greenfield. Most of this exists as scaffolding.**

### Cortex (`netie` / `CortexOS`)
| Capability | Where | State |
|---|---|---|
| Model routing T0–T3 + JudgmentModel + adapter registry | `CortexOS/execution/model_router.py`, `netie.routing.*` | Partial (rules-v0 judgment) |
| Self-hosted / vLLM adapter (OpenAI-compat via LiteLLM, MYR cost) | `CortexOS/routing/adapters/vllm.py`, `openai.py`, `anthropic.py` | Shipped (adapter), partial wiring |
| **3-tier memory**: Working(Redis)/Episodic(Qdrant)/Semantic(Postgres) + EMA personalization + context-window builder | `CortexOS/personality/memory.py` | Partial (protocols + Redis/in-mem store; Qdrant/PG stubs) |
| Hybrid RAG: dense(Qdrant/BGE-M3) + sparse(BM25) + RRF + reranker | `CortexOS/rag/*`, `CortexOS/nlp/embedder_bge.py` | Partial, not wired to demo |
| Compliance engine (YAML→deterministic) | `CortexOS/compliance/` | Shipped |
| DAG runner, cost ledger | `CortexOS/execution/`, `CortexOS/routing/` | Partial |
| WASM sandbox | `CortexOS/`, `wasm_modules/` | Scaffold |
| Optional-dep groups: `rag`(qdrant,tantivy), `local-inference`(llama-cpp), `gpu`(torch), `postgres`, `personality`(redis), `tokens`(tiktoken) | `pyproject.toml` | Present |

### AirGPT (the driver + a lot of memory thinking already written)
| Capability | Where | State |
|---|---|---|
| **Netie Engine Hosting UI** (hero + `(i)` specs + `+` engine marketplace + per-engine model market, liquid glass) | `index.html showHostingPage()`, `netie_engine.py`, `/api/hosting/engines|specs`, `/api/hosting/engine/add|remove` | **Shipped 2026-07-18** |
| Engine catalog + hardware fit + live detect + merged spec | `netie_engine.py` | Shipped |
| Hardware probe (RAM/VRAM/disk/tier) + device-fit marketplace + background install | `model_probe.py`, `marketplace.py`, `serve_profile.py` | Shipped |
| **Memory layer design** (SQLite+FTS5, Carry-pack JEPA-light compression, MemPalace wings, dual-brain company wing, mem0-compatible shape, MEXT hot/warm/cold, DSA context) | `docs/memory-layer.md`, `memory_index.py`, `vault.py`, `db.ditch_context` | Design + partial code |
| Local-inference posture (profile ladder, **TurboQuant KV flags**, vLLM, Colibri) | `docs/local-inference.md` | Design |
| Scaling ladder SQLite→pgvector/redis | `docs/SCALING_POSTGRES_REDIS.md` | Design |
| Colibri (disk-streamed MoE runtime) | `third_party/colibri` (submodule) | Vendored |

**Takeaway:** the *concepts* the user listed are already named across these files. This plan's job is to **unify them into one selectable engine + one persistent brain, complete the partial pieces, and bench them** — not to invent from zero.

---

## 3. Target architecture — "Netie Engine Up"

```
                         ┌─────────────────────────────────────────────┐
 AirGPT (driver/UI)      │  Hosting page · (i) specs · + marketplace     │
                         │  chat · agents · audit · hub (business)       │
                         └───────────────┬─────────────────────────────┘
                                         │  CortexOS API (/api/engine/*, /api/brain/*)
   ┌─────────────────────────────────────┴─────────────────────────────────────┐
   │                         NETIE ENGINE (netie.engine)                          │
   │  Selection & Capability Registry  ·  Auto-profile  ·  Specs aggregator       │
   ├───────────────────────────────┬─────────────────────────────────────────────┤
   │        INFERENCE PLANE          │                 MEMORY PLANE (netie.brain)    │
   │  backends: vLLM · SGLang ·      │  tiers: Working(hot) · Episodic(warm) ·       │
   │           Ollama · Colibri ·    │         Semantic(cold) · MemPalace(verbatim)  │
   │           llama.cpp             │  stores: sqlite-vec · Qdrant · pgvector ·     │
   │  optimizers (toggle):           │          Mongo · raw-mmap KNN                 │
   │   KV-quant(TurboQuant/KIVI/…) · │  compress: scalar-quant · Matryoshka ·        │
   │   eviction(SnapKV) · PagedAttn ·│            Carry-pack(JEPA-light)             │
   │   weight-quant(AWQ/GPTQ) · MoE ·│  tiering: MEXT-style predictive offload       │
   │   DSA sparse-context            │  dual-brain: personal + company(role-labeled) │
   ├───────────────────────────────┴─────────────────────────────────────────────┤
   │   MEMORY-TIER MIDDLEWARE (OOM-guard): intercept alloc → HBM/VRAM→RAM→NVMe      │
   │   predictive prefetch · layer/KV slicing · pipelined offload (aiDAPTIV analog) │
   └───────────────────────────────────────────────────────────────────────────────┘
```

Two planes, one registry, one OOM-guard middleware underneath both (weights *and* KV *and* vectors all tier through the same hot→cold logic).

---

## 4. Component catalog — every listed tech → home / reuse-or-build / toggle

| Component | Plane | Home (`netie.*`) | Reuse / Build | Toggle key | Dep |
|---|---|---|---|---|---|
| **vLLM** | Inference | `engine.backends.vllm` | Reuse `routing/adapters/vllm.py`; add Docker-run manager (Win = WSL2 GPU) | `engine.backend=vllm` | docker |
| **SGLang** | Inference | `engine.backends.sglang` | Build adapter (OpenAI-compat) + Docker manager | `engine.backend=sglang` | docker |
| **Ollama** | Inference | `engine.backends.ollama` | Build thin adapter (native install) | `engine.backend=ollama` | — |
| **Colibri** | Inference | `engine.backends.colibri` | Reuse `third_party/colibri` | `engine.backend=colibri` | vendored |
| **llama.cpp** | Inference | `engine.backends.llamacpp` | Reuse profile ladder; GGUF + TurboQuant flags | `engine.backend=llamacpp` | local-inference |
| **KV-Cache Optimization** | Inference | `engine.kv` | Build KV policy layer; delegate to backend flags | `engine.kv.mode` | — |
| **Google TurboQuant** | Inference | `engine.kv.turboquant` | Wire llama.cpp `--cache-type-k/v turbo3` (docs/local-inference.md) | `engine.kv=turboquant` | local-inference |
| **KIVI / QTIP / IsoQuant / PlanarQuant** | Inference | `engine.kv.*` | Research→adapter; backend-native where available | `engine.kv=<flavor>` | — |
| **SnapKV / eviction** | Inference | `engine.kv.evict` | Research→backend flag | `engine.kv.evict=snapkv` | — |
| **PagedAttention** | Inference | (vLLM/SGLang native) | Reuse (orthogonal to quant) | auto with vLLM/SGLang | — |
| **AWQ / GPTQ (weight quant)** | Inference | `engine.weights` | Reuse marketplace `quant_hint`; select at model install | `model.quant=awq\|gptq` | — |
| **MoE + DeepSeek DSA** | Inference | `engine.moe`, `engine.context.dsa` | DSA = reuse AirGPT DSA-style context (`db.ditch_context`); MoE = Colibri/backend | `context.dsa=on` | — |
| **MEXT / aiDAPTIV tiering** | Both | `engine.tiering` | Build OOM-guard middleware (see §5b) | `tiering=predictive` | — |
| **Qdrant** | Memory | `brain.stores.qdrant` | Reuse `rag/retriever_dense.py` + episodic memory | `brain.store=qdrant` | rag |
| **SQLite (sqlite-vec)** | Memory | `brain.stores.sqlitevec` | Build (default personal store) | `brain.store=sqlitevec` | — |
| **MongoDB Atlas Vector** | Memory | `brain.stores.mongo` | Build adapter (MERN/JSON metadata path) | `brain.store=mongo` | mongo |
| **PostgreSQL pgvector** | Memory | `brain.stores.pgvector` | Build (business scale; reuse Postgres semantic tier) | `brain.store=pgvector` | postgres |
| **Brute-force KNN (raw mmap)** | Memory | `brain.stores.rawknn` | Build (serialized `.bin` + metadata sidecar; §5c) | `brain.store=rawknn` | numpy |
| **Mem0** | Memory | `brain.mem0` | Reuse mem0-compatible shape (docs/memory-layer.md) | `brain.mem0=on` | mem0 |
| **MemPalace** | Memory | `brain.mempalace` | Reuse vault wings `data/vault/{proj}/rooms/{topic}` | `brain.mempalace=on` | — |
| **Scalar Quantization (int8)** | Memory | `brain.compress.sq` | Build (75% RAM cut, ~98% recall) | `brain.compress=sq` | — |
| **Matryoshka truncation** | Memory | `brain.compress.mrl` | Build (1536→256 dim) | `brain.compress.mrl=<dim>` | — |
| **Carry-pack (JEPA-light)** | Memory | `brain.carry` | Reuse AirGPT carry summaries | always-on | — |
| **Dual-brain (personal/company)** | Memory | `brain.dual` | Build on MemPalace + role labels | `brain.scope=personal\|company` | — |

New `pyproject.toml` optional-dep groups to add: `mongo=[pymongo]`, `sqlitevec=[sqlite-vec]`, `mem0=[mem0ai]`, `engine=[docker]`.

---

## 5. Research synthesis (the knowledge Fable 5 needs)

### 5a. KV-cache & quant taxonomy → decision matrix
Five orthogonal levers; they **stack** (e.g. AWQ weights + TurboQuant KV + PagedAttention simultaneously).

| Lever | Method | What it compresses | Trade-off | When to pick (our hardware: RTX 4070, 12 GB) |
|---|---|---|---|---|
| **KV quant/hash** | TurboQuant | KV tensors (global Haar rotation) | Best accuracy (Needle 0.997) | **Default** for 4K–32K ctx on 8–24 GB GPU |
| | KIVI | 2-bit per-channel/token | More compression, Needle 0.981 | Very long ctx, accept small loss |
| | QTIP | rotation + optimal distortion | Native in ExLlamaV3 | If using ExLlama backend |
| | IsoQuant / PlanarQuant | unified 5×–80× KV | multi-framework | extreme long-context |
| **Eviction** | SnapKV | drops low-attn tokens | up to 3.6× faster, lossy (0.858) | throughput > fidelity, short answers |
| | TriAttention | attn restructure | architectural | research track |
| **Math frame** | RotorQuant | Clifford rotors (3D block) | 10–19× vs cuBLAS, 44× fewer params | research/benchmark track |
| **Paging** | PagedAttention | physical KV layout | orthogonal, always safe | **always-on** with vLLM/SGLang |
| **Weight quant** | AWQ | weights 3–4 bit | stack w/ KV | **default** GPU serve of 7–8B |
| | GPTQ | weights + calibration | needs calib set | when AWQ unavailable |

**Rule:** ship TurboQuant(KV) + AWQ(weights) + PagedAttention as the auto GPU profile; expose KIVI/SnapKV/RotorQuant/QTIP as advanced toggles behind a "research" flag with a bench-before-default gate.

### 5b. Memory tiering = the real OOM-avoidance engine (MEXT / Phison aiDAPTIVLink analog)
The user's core want ("run large models without OOM") is **not** just quant — it's a **tiered-memory middleware** that catches OOM and offloads to NVMe. Pattern to build as `netie.engine.tiering`:

1. **Intercept allocation** below the runtime: when VRAM/HBM pool is exhausted, **catch the OOM before it crashes** and route the block to a lower tier (VRAM→RAM→NVMe). (aiDAPTIVLink sits below PyTorch; we sit below the backend or use backend offload hooks + our own KV/vector paging.)
2. **Predictive prefetch:** a small transformer/heuristic watches access patterns, anticipates which *cold* KV pages / experts / vectors are needed next, and stages them to the faster tier **just before** the request (cuts TTFT).
3. **Slice + pipeline:** treat model layers / KV / MoE experts as slices — GPU holds only the *active* slice; layer N+1 prefetched from SSD while N computes, N−1 evicted. (Colibri already does expert-streaming — reuse; generalize to KV + vectors.)
4. **Transparency:** appears as one contiguous "effective memory" to the app; **increase effective capacity 2–4×**; zero model-code changes.
5. **Worked target (our box):** 12 GB VRAM + large NVMe. A model+KV of ~30–40 GB → keep ~8 GB active slices in VRAM, ~2 GB safety buffer, remainder async-offloaded across PCIe. GPU never runs dry.

**Build note:** full kernel-level interception is a research effort. **MVP = orchestrate existing offload knobs** (vLLM `--cpu-offload-gb`, `--swap-space`; llama.cpp `--n-gpu-layers`; Colibri expert streaming) behind one `tiering` policy + our own **KV/vector page cache** on NVMe with predictive prefetch. Escalate to deeper interception only if the MVP's TTFT/throughput bench demands it. `address-space / memory-mapping` (user flagged "learn later") is the deep-dive owned by phase E4 — see §6.

### 5c. Vector / memory store selection → decision matrix
| Store | Arch | Idle RAM | Host cost | Pick when |
|---|---|---|---|---|
| **SQLite + sqlite-vec** | in-process file | **~0 (uses app mem)** | **$0 (disk file)** | **Personal-laptop default**, <500k vectors |
| **Raw mmap + brute-force KNN** | serialized `.bin` + metadata | ~0 (OS page cache) | $0 | <~10k vectors, sub-second dot/cosine on CPU/GPU; no index graph to load |
| **Qdrant (mmap)** | dedicated Rust, on-disk HNSW | Low (vectors on disk) | ~$5 VPS | Business scale, low-RAM server, fast query |
| **MongoDB Atlas Vector** | document/NoSQL | Medium (managed cache) | Free tier | MERN/JSON metadata, flexible schema |
| **Postgres pgvector** | relational | High (RAM for HNSW graph) | Medium managed | Already on Postgres; transactional + vector |

**Compression (stack on any store):** Scalar Quant float32→int8 = **−75% RAM/disk, ~98% recall** (Qdrant/pgvector native). Matryoshka: truncate 1536→256 dim on MRL models (Nomic, `text-embedding-3-*`) = proportionally smaller index.

**Brute-force vs index:** below ~10k vectors, **brute-force KNN (dot/cosine, vectorized numpy or GPU) beats loading a heavy HNSW graph** — no graph build/load cost, exact recall. Crossover ~10k–50k → switch to sqlite-vec/Qdrant HNSW. The engine picks automatically by collection size.

**Storage layout for raw/mmap tier (answers user's "serialize embeddings into raw sequential files"):**
```
data/brain/{scope}/{collection}/
  vectors.bin      # float32/int8, row-major, fixed dim → mmap, seek by offset
  meta.sqlite      # id → {offset, text_ref, tags, created_at, ttl, tier}
  chunks.db        # verbatim text (SQLite or RocksDB)
```
mmap the `.bin`, read the needed row range from fast storage, evict hot rows from RAM to free memory (Google "scaled memory mapping" analog: OS page cache does the tiering; we control eviction hints).

### 5d. Dual-brain / persistent company memory
- **Personal:** `brain.scope=personal`, laptop-local, hidden, always-on ingest of substantive turns (reuse AirGPT `persist_turn_memory` + carry-pack). Embeddings retained; thin/forget only demotes to cold summaries, never hard-deletes learning by default.
- **Company:** `brain.scope=company`, collaborative wing (`data/vault/_company/rooms/*`). Every contribution **role/position-labelled** at write time from the assigned-access identity (reuse AirGPT `access_policy` + hub roles; Cortex RBAC + RLS from CORTEX_COMPLETE_PLAN Phase 3). Verified snippets → dual-brain feedback wing → become the company's persistent skill memory.
- **Leakage monitoring (business):** hub feature — flag cross-scope reads, PII in prompts (reuse F7 PII choke-point), and unusual export patterns; surfaced in Usage/Spot-check (already in AirGPT).

---

## 6. Phased execution roadmap
Two tracks run in parallel: **E** (Inference/engine) and **M** (Memory/brain). Each phase: **build in Cortex → expose API → wire AirGPT → bench gate**. Mirror the discipline of `CORTEX_COMPLETE_PLAN.md` (acceptance + anti-scope + gate).

### Track E — Inference Plane
| Phase | Goal | Key work | Acceptance / bench gate |
|---|---|---|---|
| **E0** | Selection registry | `netie.engine.registry` — capability descriptors, auto-profile from `model_probe`, one `specs()` aggregator | `/api/engine/specs` returns merged caps; AirGPT `(i)` reads it (replace `netie_engine.specs()` shim) |
| **E1** | Real backends live | Ollama adapter + install manager; vLLM & SGLang Docker managers (WSL2 GPU); Colibri bind | Each backend serves an OpenAI `/v1` completion; engine box flips **Running**; TTFT logged |
| **E2** | KV + weight optimizers | `engine.kv` policy (TurboQuant default) + AWQ/GPTQ select at install; PagedAttention auto | Long-ctx (16k) fits 12 GB w/ TurboQuant; bench Needle recall ≥ 0.95; VRAM headroom report |
| **E3** | DSA sparse-context + MoE | Reuse AirGPT DSA ditch/index; Colibri MoE streaming behind `engine.moe` | 32k logical ctx served on 12 GB; no OOM; recall on pinned facts ≥ target |
| **E4** | **Tiering / OOM-guard** (MEXT analog) | `engine.tiering`: orchestrate offload knobs + NVMe KV/vector page cache + predictive prefetch. *Owns the address-space/mmap deep-dive.* | A model+KV ~2–3× VRAM runs without OOM; TTFT within X% of in-VRAM; throughput report |
| **E5** | Advanced toggles + research bench | KIVI/QTIP/SnapKV/RotorQuant as opt-in; auto-bench harness gates any promotion to default | Each toggle benched (recall, TTFT, tok/s, VRAM) vs baseline; results in `docs/research/kv_bench.md` |

### Track M — Memory Plane
| Phase | Goal | Key work | Acceptance / bench gate |
|---|---|---|---|
| **M0** | Store abstraction | `netie.brain.store` protocol (upsert/query/evict) + capability flags; wire existing Qdrant retriever | One interface, 2 impls (sqlite-vec, qdrant) pass a shared conformance test |
| **M1** | Personal default stack | sqlite-vec store + raw-mmap KNN tier + auto crossover by size; SQLite+FTS5 hybrid (reuse memory-layer.md) | <500k vectors on laptop, ~0 idle RAM; query p95 < 50 ms @ 100k; recall@10 bench |
| **M2** | Compression | Scalar-quant (int8) + Matryoshka truncation toggles | −75% footprint at ≥97% recall@10 on a fixed eval set |
| **M3** | Tiered persistence + always-learn | Working(Redis/in-mem)→Episodic(store)→Semantic(PG/SQLite) promotion; MEXT hot/warm/cold eviction; carry-pack | Turn ingest never blocks chat; cold retrieval < 200 ms; nothing hard-deleted unless retention policy fires |
| **M4** | Dual-brain + roles (business) | `brain.scope` split; role/position labels at write; RLS/RBAC (CORTEX Phase 3) | Viewer cannot read steward-only company memory even via direct DB; every write role-stamped |
| **M5** | Leakage monitor | Hub flags: cross-scope read, PII-in-prompt, anomalous export | Synthetic leak attempts flagged in Spot-check; audit chain entry per flag |
| **M6** | Scale swap | pgvector / Qdrant server / Mongo backends selectable for business; same interface | Same conformance test green on all backends; migration script personal→server |

**Cross-cut:** every phase writes to the **bench harness** (§7) and appends a one-line result to `docs/research/netie_bench.md`. No toggle becomes a *default* without a passing bench.

---

## 7. Benchmark & debug harness (test everything, every memory layer)
Build `netie.bench` + `tests/bench/` early (it is the gate for every phase).

| Dimension | Metric | Tool |
|---|---|---|
| **OOM safety** | max model+KV size served without crash on 12 GB | `bench/oom_ladder.py` — grow ctx/model until fail; assert tiering catches it |
| **Latency** | TTFT, tok/s, p50/p95 | LiteLLM timing (adapter already records `t0`) |
| **KV recall** | Needle-in-haystack score per KV mode | `bench/needle.py` |
| **Vector recall** | recall@10, NDCG@10 vs exact brute-force ground truth | `bench/vector_recall.py` |
| **Memory footprint** | idle RAM, disk, VRAM per store/compress combo | `bench/footprint.py` (psutil + nvidia-smi) |
| **Tiering** | prefetch hit-rate, offload latency, TTFT delta vs in-VRAM | `bench/tiering.py` |
| **Persistence/learning** | recall of a fact after N sessions / thin cycles | `bench/longterm.py` |
| **Leakage (business)** | % synthetic leaks flagged; false-positive rate | `bench/leakage.py` |

**Debug affordances:** `/api/engine/trace` (per-request tier decisions, KV mode, offload events), a `NETIE_DEBUG=1` verbose mode, and the `(i)` specs popover doubles as a live health/telemetry surface. AirGPT verification uses the in-app Browser pane (screenshots time out on the heavy SPA → verify via DOM/JS + endpoint checks, per this session's method).

---

## 8. Interface contracts (Cortex ↔ AirGPT)
New CortexOS API (AirGPT calls these; today AirGPT has local `netie_engine.py` shims to replace):
```
GET  /api/engine/specs                 → merged capability + hardware + optimizer state  ((i) popover)
GET  /api/engine/backends              → available/added backends + live status
POST /api/engine/backend/{add|remove}  → toggle a backend into Netie Engine
POST /api/engine/config                → set toggles {backend, kv, weights, tiering, moe, dsa}
GET  /api/engine/trace                 → last-N routing/tiering decisions (debug)
POST /api/brain/upsert                 → {scope, collection, text, meta}
GET  /api/brain/query                  → {scope, collection, q, k, compress}
POST /api/brain/config                 → {store, compress, mrl_dim, tiering}
POST /api/brain/forget                 → {thin|retention}
```
AirGPT wiring: the shipped Hosting UI (`+` marketplace, `(i)` specs, per-engine model button) points at these once Cortex owns them; the model-marketplace facets (LLMs/Agents/Engines/Connectors + "supported by my device") map to `brain`/`engine`/marketplace categories.

---

## 9. Open questions (need answers to prioritize execution)
1. **Track order:** Engine plane first (title says "build Netie Engine *then* Up"), Memory plane first (the persistent dual-brain is the moat), or interleaved E0→M0→E1…?
2. **v1 target:** Personal-laptop dual-brain first, or business-collaborative first? (Drives default store: sqlite-vec vs Qdrant/pgvector, and whether RBAC/RLS is phase-1.)
3. **OOM-guard depth:** Is the **MVP** (orchestrate existing offload knobs + our NVMe page cache) enough for v1, or is deep kernel-level interception (true aiDAPTIV-style) a hard requirement now? (Huge effort difference.)
4. **Selectable granularity:** confirm "auto-default + advanced toggles behind a research flag," or must *every* lever be a first-class user choice from day 1?
5. **Hosting reality:** personal = client's own laptop (confirmed). Business = client laptop as host *or* a $5 VPS / their NAS? (Affects Qdrant-server vs embedded.)
6. **Scale numbers:** rough vectors/rows per personal brain and per company brain? (Sets the brute-force→HNSW→server crossovers.)
7. **Windows-first:** vLLM/SGLang are Docker-only on Windows (WSL2 GPU). Is Docker/WSL2 an acceptable dependency for personal users, or must the *native* personal path be Ollama/llama.cpp only (Docker reserved for business/power)?

## 10. Fable 5 orchestration hooks (goal-driven execution scaffolding)
Structure so Fable 5 can pick a goal and drive:
- **Goal graph:** each §6 phase = a node with `{acceptance, anti_scope, bench_gate, files}`. Fable 5 selects a node, plans, executes, and cannot mark done until the bench gate (§7) passes.
- **Parallelism policy (inherit CORTEX_COMPLETE_PLAN §7):** research subagents parallel (markdown to `docs/research/`); code/migration subagents sequential, one gate between each.
- **Registry-first:** E0 + M0 (the two abstractions) are the unblockers — build them before any backend/store so every later phase plugs into a stable interface.
- **Bench-as-contract:** `docs/research/netie_bench.md` is the running scoreboard; a toggle is "default-eligible" only with a green bench row.
- **Autonomy budget:** low-risk (add adapter behind a flag, write a bench) → proceed; hard-to-reverse (new heavy dep, default change, deleting learned memory) → gate to user.

---
*Authored 2026-07-18 as a plan for Fable 5. Update the inventory (§2) as pieces ship; keep planned ≠ shipped.*
