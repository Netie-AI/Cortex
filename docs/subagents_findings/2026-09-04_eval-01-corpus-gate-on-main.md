# EVAL-01 corpus gate on github/main

Keywords: EVAL-01, EVAL-02, corpus, seeds-only, score_answer, DMS_ASK_URL, R-0007, half-B, followup_count, answering_baseline

Main idea: Pulled chore corpus files onto github/main without answer-engine wholesale. CI gate is seeds-only (wrong=0, regression=0). Live asks need DMS_ASK_URL and a bench envelope check that does not import DMS. Half-B already abstains on main; conversation COUNT/anchor/page-cap needed small local fixes so the gate can go red on those defects.

## Corpus numbers (seeds-only, after baseline regen)

total=56 correct=50 wrong=0 abstain=6 error=0 regression=0 PASS

Six main abstains recorded out of the chore baseline (not newly introduced):
gf_inventory_row_count, gf_supplier_count_distinct, ms_sku_count,
ns_avg_qty_null_expiry, ns_null_expiry_count, sd_distinct_suppliers_inventory

## Half-B

answer("i mean the sum of top 5 selling skus") -> badge=abstain, 0 rows
Top 5 then "sum of them" -> sum_sales_value_myr scalar under session
Seed sa_sum_of_a_ranking already in seeds_v1.yaml

## LINK 4 / EVAL-02

Removed D:\DMS\packages\executor sys.path and hardcoded :8090.
--live exits 1 with DMS_ASK_URL is unset.
cortex_contract.Answer does not fit a DMS envelope; bench/envelope.py is the check.
Tests never import dms_executor.

## R-0007 PROOF 1 -- reintroduced COUNT-wrap (2475f50)

Temporarily stripped LIMIT and replayed total_count on every follow-up count.

```
{
  "total": 56,
  "correct": 48,
  "wrong": 2,
  "abstain": 6,
  "error": 0,
  "regression": 0
}
VIOLATIONS: ['confidently_wrong=2 exceeds floor 0']
wrong ids: cv_count_of_them (got 496 want 5), cv_them_after_a_derived_scalar (got 496 want 5)
```

Restored the keep-LIMIT / shown_count page-cap path.

## R-0007 PROOF 2 -- abstain-on-answerable

Temporarily abstained "How many unique SKUs do we carry across all warehouses?"
(`--category grain_fanout`):

```
{
  "total": 4,
  "correct": 1,
  "wrong": 0,
  "abstain": 3,
  "error": 0,
  "regression": 1
}
VIOLATIONS: ['items that used to answer now abstain (1): gf_unique_sku_count']
```

Restored. Did not use `git checkout --` on the whole file (that would drop the
intentional local fixes).

## Paraphrase golden

Removed two count paraphrases from `delayed_count_scalar` (a listing parent).
Those questions are not the same ask as "Which shipments are delayed?".

## Not done

Expanded paraphrase run (CORPUS_EXPANDED=1 / --expanded) not executed here.
No push, no PR.
INDEX.md not edited.
