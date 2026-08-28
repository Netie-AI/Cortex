# Cortex → DMS — C10 kickoff (adversarial eval + CI ratchet)

**Status:** C10 harness expanded — 11-category `bench.adversarial` + value_normalization golden; paraphrases ~100 (target 150–300).  
**Not sellable C10 yet** until floor raised and corpus grown.

## Shipped

| Piece | Where |
|-------|--------|
| Paraphrase zero-wrong gate | `tests/dms/test_paraphrase_benchmark.py` |
| Robustness floor | `bench/golden/paraphrase_baseline.json` (raise-only) |
| Adversarial harness | `bench/adversarial.py` + `dms_adversarial_v1.yaml` |
| Category CI | `tests/dms/test_adversarial_benchmark.py` |
| Hostile SQL corpus | C3 (`hostile_sql_corpus.json`) — not answer-quality |

## Still open

- Grow paraphrases to 150–300; raise robustness floor when earned
- Plausibility stage (needs **C8** `query_run` history)

## DMS

No DMS change required for C10-min. Live smoke stays independent.
