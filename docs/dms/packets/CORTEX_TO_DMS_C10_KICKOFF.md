# Cortex → DMS — C10 kickoff (adversarial eval + CI ratchet)

**Date:** 2026-07-30  
**Status:** C10-min started on Cortex — paraphrase `wrong==0` + robustness floor in pytest.  
**Not sellable C10 yet.**

## Shipped (C10-min)

| Piece | Where |
|-------|--------|
| Paraphrase zero-wrong gate | `tests/dms/test_paraphrase_benchmark.py` |
| Robustness floor | `bench/results/paraphrase_baseline.json` (raise-only) |
| Hostile SQL corpus | already C3 (`hostile_sql_corpus.json`) — not answer-quality |

## Still open (sellable / full C10)

- Grow paraphrases toward 150–300
- Golden adversarial abstain tier
- Plausibility stage (needs **C8** `query_run` history)
- Unified `bench.adversarial` entrypoint

## DMS

No DMS change required for C10-min. Live smoke stays independent.
