# D1 + D5 + D6 — sqlite-vec, brute-force crossover, raw-file layout

**Date:** 2026-07-18  
**Gate:** Unblocks personal memory default (`brain.store=sqlitevec` + `rawknn`)  
**Hardware context:** Personal laptop class (32 GB RAM, Win11, Python 3.10)  
**Status:** RESEARCH COMPLETE — latency cells marked MEASURED (cited) vs ESTIMATED.

**LOCAL RE-BENCH 2026-07-19 (i5-13490F, criteria #2/#3):** `rawknn` mmap store
(`CortexOS/memory/stores/rawknn.py`, D6 layout + norms.bin sidecar) measured
on-box, warm, exact top-1 verified: **10k×384 → 4.3 ms**, **100k×384 → 76.8 ms**
per query (unfiltered fast path: no-copy `mm @ q` + top-k-only SQL fetch).
100k lands slightly above the 50 ms Apple-silicon figure but the ≤100k
brute-force crossover holds on this CPU. Implementation traps found:
(1) never open `vectors.bin` in append mode — O_APPEND breaks in-place row
overwrite (writes silently go to EOF); (2) per-query full-table SQL candidate
fetch dominates at 100k (was 284 ms) — score first, fetch only top-k rows.

---

## D1 — sqlite-vec (personal default)

| Question | Finding |
|---|---|
| ANN or brute-force? | **Brute-force KNN** today (`vec0`). ANN (IVF / DiskANN) tracked in [#25](https://github.com/asg017/sqlite-vec/issues/25); not required for v1 personal. |
| Practical ceiling | Author guidance: **hundreds of thousands** vectors comfortable; **~1M** is where BF slows hard (esp. high dim). Plan's **&lt;500k** personal default is sound. |
| Quant support | **float, int8, binary** in `vec0` — use int8/binary to stretch ceiling. |
| Windows | Official loadable: `sqlite-vec-*-loadable-windows-x86_64.tar.gz` (e.g. v0.1.7). Also PyPI/`sqlite-vec` packaging path. |
| Concurrent R/W | Same as SQLite: one writer; many readers with WAL. App process can hold the connection; use WAL + busy timeout. Do not multi-writer without queue. |
| Idle RAM | ~0 beyond SQLite page cache / mmap_size — matches "file-on-disk personal" goal. |

**Sources:** [asg017/sqlite-vec](https://github.com/asg017/sqlite-vec), [v0.1.0 announce](https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/index.html), [v0.1.7 release](https://github.com/asg017/sqlite-vec/releases/tag/v0.1.7).

**M1 recommendation:** Default `brain.store=sqlitevec` for personal; enable int8 when embedder pipeline supports it; promote to Qdrant only for business/scale.

---

## D5 — Brute-force crossover

### Published small-scale benches (MEASURED, dim≈384-class, cited)

From [DadOps: Vector Search at Small Scale](https://daddaops.com/blog/vector-search-benchmarks/) (NumPy vs FAISS Flat/HNSW):

| N | NumPy (ms) | FAISS Flat (ms) | FAISS HNSW (ms) |
|---|---|---|---|
| 10k | 0.30 | 0.08 | 0.04 |
| 50k | 1.5 | 0.40 | 0.09 |
| 100k | 3.1 | 0.80 | 0.16 |

**Takeaway:** At ≤10k, brute force is trivially sub-ms–few-ms. HNSW wins on *query* latency earlier, but **build + RAM for graph** is wasted when N is small or updates are frequent ([FAISS wiki](https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index): if few searches, Flat wins on total cost).

### FLOP-back-of-envelope (ESTIMATED)

Cosine/dot for one query ≈ `2 * N * D` FLOPs (mul+add).  
At ~50 GFLOP/s effective CPU BLAS (conservative laptop):

| N \ D | 256 | 768 | 1536 |
|---|---|---|---|
| 1k | ~0.01 ms | ~0.03 ms | ~0.06 ms |
| 10k | ~0.1 ms | ~0.3 ms | ~0.6 ms |
| 100k | ~1 ms | ~3 ms | ~6 ms |
| 1M | ~10 ms | ~30 ms | ~60 ms |

GPU (torch matmul) typically **5–20×** faster for large N×D — use when collection hot and CUDA already loaded; not required for personal &lt;10k.

### Auto-selection rule → `netie.brain.store`

```text
if N < 10_000:
    store = rawknn          # mmap .bin + brute-force (exact); no graph
elif N < 500_000:
    store = sqlitevec       # BF in-process; int8 optional
else:
    store = qdrant          # business / on-disk HNSW (mmap)
# Optional: if query_p95 > 50ms OR build_cost amortized → promote early
```

Aligns with plan §5c (crossover ~10k–50k) and FAISS "few searches → Flat" guidance. **User claim "10k within a second"** is conservative — real BF is usually <<1 s even at 100k×768 on CPU.

---

## D6 — Raw-mmap layout + chunk store

### On-disk layout (final proposal)

```text
data/brain/{scope}/{collection}/
  manifest.json       # dim, dtype, count, capacity, embedder_id, version
  vectors.bin         # row-major fixed-width; no framing
  meta.sqlite         # id → offset_row, text_ref, tags, created_at, ttl, tier, hash
  chunks.sqlite       # id → verbatim text (+ optional FTS5)
  # optional later:
  # vectors.int8.bin  # parallel quantized shadow
```

### `vectors.bin` row format

| dtype | bytes/dim | row bytes | offset |
|---|---|---|---|
| float32 | 4 | `dim * 4` | `id * dim * 4` |
| int8 | 1 | `dim * 1` | `id * dim` (+ scale in meta or header) |
| binary | 1/8 | `ceil(dim/8)` | packed bits |

**Header:** keep in `manifest.json` (not inside mmap) so remap after grow is trivial. Optional 4 KiB file header reserved if you need self-describing single-file export.

**Append:** write row at `count`, `count++`, fsync policy = batch; grow `capacity` in chunks (see A1).

### Chunk / meta store: SQLite vs RocksDB

| | SQLite (`meta.sqlite` + `chunks.sqlite`) | RocksDB |
|---|---|---|
| Idle RAM | Very low | Higher (block cache defaults) |
| Windows | Excellent / stdlib | Good but heavier native dep |
| Write amp | Low–moderate (WAL) | Higher under random writes |
| FTS / SQL filters | **FTS5 native** (AirGPT memory-layer already oriented here) | Need secondary index |
| Ops complexity | Minimal | Higher |

**Decision: SQLite for both meta and chunks in v1.** RocksDB only if write-heavy compaction benches fail (unlikely for personal dual-brain).

### Compaction

- **vectors.bin:** append-only; tombstones in `meta.sqlite` (`deleted=1`); periodic rewrite when tombstone fraction &gt; 20%.  
- **SQLite:** `VACUUM` / incremental vacuum on schedule; WAL checkpoint after bulk ingest.

---

## Gate flip criteria

| Criterion | Pass? |
|---|---|
| sqlite-vec is BF + Windows loadable + int8/binary | Yes |
| Personal ceiling &lt;500k documented | Yes |
| Crossover rule N&lt;10k → rawknn | Yes (supported by benches) |
| Layout + SQLite chunk store specified | Yes |

**Gate: GREEN for M1 personal default.** Next: conformance tests on stub → sqlite-vec → rawknn.

---

## Sources

- https://github.com/asg017/sqlite-vec  
- https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/index.html  
- https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index  
- https://daddaops.com/blog/vector-search-benchmarks/  
- Companion: `A1_A2_mmap_pagecache.md`
