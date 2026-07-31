# Benchmark inventory — external eval datasets

**Status:** Phase 2 prep (document before adapter code)  
**Last verified:** 2026-07-31  
**Rule:** If schema differs from this doc, stop and update here before writing adapters.

---

## CRAG — Comprehensive RAG Benchmark

| Field | Value |
|---|---|
| **Local path** | `D:\DMS\tests\repos\CRAG` |
| **Upstream** | [facebookresearch/CRAG](https://github.com/facebookresearch/CRAG) (Meta KDD Cup 2024) |
| **Licence** | Check repo `LICENSE` before redistribution |
| **What it measures** | Document QA with retrieval — chunking, hybrid retrieval, abstention calibration |
| **What it does NOT measure** | NL→SQL, `_src` propagation, drill-through, governed metrics |

### Schema (from `docs/dataset.md`)

| Field | Type | Notes |
|---|---|---|
| `interaction_id` | string | Unique example id |
| `query_time` | string | Query + search timestamp |
| `domain` | string | `finance` \| `music` \| `movie` \| `sports` \| `open` |
| `question_type` | string | `simple`, `simple_w_condition`, `comparison`, `aggregation`, `set`, `false_premise`, `post-processing`, `multi-hop` |
| `static_or_dynamic` | string | `static`, `slow-changing`, `fast-changing`, `real-time` |
| `query` | string | Question text |
| `answer` | string | Gold answer |
| `alt_ans` | list | Alternate valid answers |
| `split` | int | 0=val, 1=public test |
| `popularity` | string | `head` \| `torso` \| `tail` \| `""` (empty = web-sourced) |
| `search_results` | list | Up to 5 (task 1) or 50 (task 3) HTML pages per query |

### Scoring rubric

| Label | Points | DMS mapping |
|---|---|---|
| Correct | +1 | Answer matches gold |
| Missing | 0 | **`abstained → MISSING`** (not incorrect) |
| Incorrect | −1 | Confident wrong answer |

**Hard gates (Phase 2):** `false_premise` subset → 100% abstention; incorrect-rate < 2%.

### DMS adapter plan (not built yet)

```
CRAG corpus → ingest to dedicated Space (blob + doc index only)
           → assert no CRAG content in silver/gold
Question   → POST /v1/chat/ask  ask_mode=live demo_fallback=false
Envelope   → assert_envelope_valid() on every response
Score      → abstained → MISSING (0), match → CORRECT (+1), else INCORRECT (−1)
Output     → bench/crag/results/<timestamp>.json + calibration curve markdown
```

### Expected operating point

DMS will score **lower correct-rate, near-zero incorrect** vs typical RAG. That is the design, not a failure. Report as a distinct trade-off.

---

## open_ragbench

| Field | Value |
|---|---|
| **Local path** | `D:\DMS\tests\repos\open_ragbench` |
| **Status** | **Not present on this machine** (2026-07-31) |
| **Action** | Clone or vendor before any adapter; record schema here first |

Several projects share the "RAGBench" name — confirm licence and question format before use.

---

## BIRD-SQL (Phase 3 — higher value)

| Field | Value |
|---|---|
| **Local path** | Not vendored yet |
| **What it measures** | Messy real-world DBs, external knowledge, execution accuracy |
| **DMS fit** | Closest to product job — maps to semantic layer + C7 schema retrieval |
| **Scoring** | Three buckets: `correct` \| `abstained` \| `incorrect` (standard leaderboards miss abstain) |

### Schema-width stress (Phase 3)

| Tables | Watch for |
|---|---|
| < 20 | Baseline |
| 20–100 | Retrieval recall |
| 100–500 | Wrong-table silent failure |
| 500+ | Latency + recall collapse |

---

## Spider 2.0

Enterprise schemas, 1000+ columns, multi-step. Phase 3 after BIRD. Spider 1.0 skipped (too easy).

---

## Internal corpora (this repo)

| Artifact | Path | Count | Phase |
|---|---|---|---|
| Golden v1 | `bench/golden/dms_golden_v1.yaml` | ~36 items | Shipped (B0) |
| Adversarial v1 | `bench/golden/dms_adversarial_v1.yaml` | ~19 items | C10 |
| Phase 1 seeds | `bench/corpus/seeds_v1.yaml` | 45 seeds (12 categories) | **1a** |
| Persona probes | `bench/live_personas.yaml` | 22 live probes | **1a** |
| Thresholds | `bench/thresholds.yaml` | CI floors | **1a** |

---

## Verification commands

```bash
# Offline corpus (no API, uses answer_question + canonical SQL)
python -m bench.corpus

# Live DMS envelope + E1–E8
python -m bench.corpus --live --dms-url http://127.0.0.1:8090

# Persona probes (full stack)
python -m bench.live_probe

# CRAG inventory check (when data downloaded)
ls D:\DMS\tests\repos\CRAG\data
```
