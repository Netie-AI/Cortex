# C7-04 held-out corpus + envelope harness

- Date: 2026-09-04
- Keywords: C7-04, held-out, BIRD, Spider, must-abstain, envelope, SKU-BETA, metrics.yaml, EPIC-006
- Main idea: Frozen `bench/heldout/c7_heldout_v1.yaml` is BIRD/Spider-style SQL plus different-model must-abstain, not team paraphrases of metrics.yaml. `bench/heldout.py` scores correct/abstained/incorrect on answer text + rows + badge. Empty rows never score correct. CI runs a tiny fixture, not live L2.
- Path: this file

## PREFLIGHT

PARTIAL. reuse: `docs/subagents_findings/2026-09-04_c7-design-and-shadow-mode.md`, `docs/subagents_findings/2026-09-04_eval-01-corpus-gate-on-main.md`. spawn: skip (executor).

## Golden rules

1. Held-out items must not be metrics.yaml questions/synonyms or `dms_paraphrase_v1.yaml` strings. Overlap guard is exact-match plus Jaccard >= 0.85 on 4+ token phrases.
2. Score the customer envelope (answer/text + rows/values + badge/route). `sql_used` matching gold is never a pass.
3. Empty `correct_rows` is incorrect (SKU-BETA / G4). Gold-empty is also incorrect.
4. Must-abstain that serves rows is incorrect. Answerable refusal is abstained, not incorrect.
5. Do not enable `DMS_L2_ENABLED`. Do not edit `sql_validate_gate.py` / `l2_generation.py` / `answer_engine.py`.

## Verify

```
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/dms/test_c7_heldout.py -q --tb=short
```

Does not prove: live FreeRoute quality, G-abs/G-err on the engine, or C7-05 serve swap.
