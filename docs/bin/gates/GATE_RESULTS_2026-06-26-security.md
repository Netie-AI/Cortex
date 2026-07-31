# Gate Results — Security + F3 wave (2026-06-26)

Full suite: **103 passed, 4 skipped**

| Gate | Verdict | Evidence |
|---|---|---|
| Adversarial security | **PASS** | 15-case corpus, injection/scam/PII |
| WASM sandbox | **PASS** | fuel limits, minimal module returns 42 |
| F3 classify | **PASS** | 7 tests, psychological_state routing |
| F7 harness upgrade | **PASS** | query_service + classify wired |
| F2 chat classify wire | **PASS** | inbound ledger includes intent |

## Residual debt
- Postgres ledger CI (DMS_LEDGER_DSN)
- SOPS + rate limiting
- Local Qwen fine-tune (GPU script ready, not trained)
- Ontology P1 — parked

## Next dispatch
Ship F4 task suggest. Parallel: Phase 0 deploy planning.
