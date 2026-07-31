# Phase 1b — paraphrase expansion and the gold-verification gate

**Status:** shipped 2026-07-31. Corpus is 376 scored items; the claim still stands at
N=47 because only the hand-written seeds are human-verified.

Companion: `docs/dms/DMS_EVAL_AND_STRESS_PLAN.md` (Phase 1.3–1.4),
`docs/eval/BENCHMARK_INVENTORY.md` (Phase 2 prep).

---

## 1. What shipped

| Artifact | Role |
|---|---|
| `bench/corpus/paraphrases_v1.yaml` | 329 paraphrases, 7 per Phase 1a seed, keyed by seed id |
| `bench/corpus.py` | Loads them, inherits parent gold, reports `claim_n` and `expanded_n` separately |
| `bench/verify_gold.py` | The human review loop that moves an item into `claim_n` |
| `bench/thresholds.yaml` | Phase `1b`; `expanded_abstain_rate_ceiling`; claim counts verified only |
| `tests/dms/test_corpus_seeds.py` | Entity-preservation, gold-inheritance and gate tests |

A paraphrase **inherits** its parent's `canonical_sql`, `match`, `key_columns` and
category. It authors no gold of its own — if it needed different gold it would be a
new seed, and the two could then drift apart without anything noticing.

## 2. The gate, and why it is not decoration

Generating 300 questions costs minutes. The claim "0 confidently wrong" rests on
someone having *read* them, so:

* every expanded item is `gold_verified: false` by default;
* `claim_n` counts verified items only — today 47, the hand-written seeds;
* `expanded_n` (376) is reported next to it and is never substituted for it;
* `confidently_wrong` is checked across **everything**, verified or not, because a
  confident wrong answer is a defect in the engine regardless of who wrote the
  question;
* the rate floors apply to the claim set, since paraphrases abstain more by design
  and holding them to the seed floor would create pressure to answer where
  abstaining is correct.

`GET /dms/eval/summary` reports `corpus_n` (verified), `corpus_expanded_n` and
`corpus_unverified_n`, and the Trust page draws two bars so the larger number can
never be mistaken for the claim.

To review:

```bash
python -m bench.verify_gold --status
python -m bench.verify_gold --review --by <name>
```

## 3. What the expansion actually caught

The first run of the expanded corpus produced **18 confidently wrong answers** on a
corpus that had been green at N=47. Every one was a real routing defect, not a
corpus authoring bug. Grouped by root cause:

| Root cause | Example that failed | Fix |
|---|---|---|
| Exclusion verbs too narrow | "leave out 00173 and give top 5 sku by revenue" produced an **unfiltered** ranking reported as success | `_excluded_skus` verb list + whitespace-aware clause tokenizer |
| Exclusion clause tokenizer | "exclude 00173 **from the** top 5" arrived as one unmatched blob | split on whitespace; filler dropped by name |
| `drop` read as DDL | "drop SKU-BETA, top 5 sku by revenue" was **blocked** — a legitimate question refused | ranking-context guard in `destructive_intent` |
| Plain-English destruction missed | "get rid of the inventory table", "blank out the suppliers table" were only `needs_clarification` | `_MUTATION_PHRASE` folding + `empty`/`clear` as storage-only verbs |
| Row count vs SKU count | "how many rows are in the inventory table" answered with a SKU count | `inventory_row_count` metric + branch, guarded against filtered counts |
| Distinct suppliers | "how many distinct suppliers are in our inventory" fell through to a skill replay | `supplier_count` metric + branch |
| NULL semantics unrouted | "count inventory rows with no expiry date" fell through to a skill replay | `null_expiry_count`, `avg_qty_null_expiry` metrics + branch |
| Digit + superlative limit | "the 3 highest SKUs by quantity sold" returned 5 rows | `_explicit_limit` accepts `<n> highest|best|…` |
| Aggregate intent | "Total cold storage locations" returned the listing | branch uses `_wants_aggregate` |
| Vocabulary gaps | "risky suppliers" answered with *shipments*; "penghantaran" unrouted | `vocabulary.py` rules |

After the fixes: **376 scored, 375 correct, 0 wrong, 1 abstain.**

Two lessons worth keeping:

1. **Falling through L1 is not safe.** Several of these did not abstain — they hit
   the query-skill replay and answered with a *previously captured* metric. An
   unrouted question is a question that gets answered by whatever looks similar.
2. **Refusing a legitimate question is a defect, not caution.** The `drop SKU-BETA`
   false positive would have taught a user the product is broken. Real enforcement
   is the sqlglot AST check in `sql_guardrail`, which no wording gets past; the
   intent layer above it should classify intent, not spot verbs.

## 4. A gap this exposed in the environment, not the engine

`CortexOS/dms/generate_sample.py` wrote only `*_messy.csv`, while
`warehouse_db.TABLE_FILES` loads `*_clean.csv`. The clean files were therefore
**unregenerable**: any run that re-seeded the warehouse from the lakehouse
migration silently dropped the seeded fixtures — including `SKU-BETA`, the entire
point of the `value_normalization` category — and nothing could put them back.
Symptom was a live corpus where every BETA question abstained with "cannot resolve
'BETA' to a sku value" while the offline run was green.

`main()` now writes both. `data/` is documented as gitignored and regenerated, so
it has to actually regenerate:

```bash
python -m CortexOS.dms.generate_sample
python -m CortexOS.dms.warehouse_db      # expect inventory 7388, 509 distinct SKUs
```

## 5. Running it

```bash
set DMS_READ_ONLY_QUERIES=1
set PACK=dms
python -m bench.corpus                 # offline, canonical SQL
python -m bench.corpus --seeds-only    # Phase 1a subset
python -m bench.corpus --live          # DMS envelope E1-E8 on :8090
python -m pytest tests/dms/test_corpus_seeds.py -q
```

Three environment conditions the live run depends on, each of which failed during
this work and each of which presents as an answer-path bug when it is not:

1. **`DMS_READ_ONLY_QUERIES=1` on the engine.** Without it every governed query
   takes an exclusive DuckDB lock and the run fills with `submit_failed` 500s.
2. **`OPENVAULT_HOME` pinned when starting OpenVault** (`D:\OpenVault\.openvault`).
   Unpinned, `POST /keys/services` returns 500, DMS cannot mint a manifest, and
   *every* live ask fails with `live_ask_failed` — while `/api/healthz` still
   answers 200, so a health check does not catch it. Start with
   `openmw console --host 127.0.0.1 --port 5000 --no-open-browser`; the
   `--mock-health` flag makes the health signal meaningless and should be off for
   anything you intend to trust.
3. **Pacing.** Cortex throttles `/dms/*` at `DMS_RATE_LIMIT_PER_MIN` (default 120)
   and one ask spends more than one token. `--rps` defaults to 0.8 for that reason;
   raise the engine's limit (`DMS_RATE_LIMIT_PER_MIN=6000`) to run at `--rps 4`.
   `throttle_retries` in the report is non-zero whenever the run was fighting the
   limiter rather than measuring the answer path.

## 6. Not done

* `claim_n` is 47. Reaching 310 is a review task, not a code task.
* Paraphrases are 7 per seed; the plan allows 8–10 if more coverage is wanted.
* No CRAG or BIRD adapter yet — Phase 2 and 3 remain gated behind this floor.
