# Gate Results — 2026-06-26

Verified by automated gate packet (Codex 5.3). Full suite: **86 passed, 4 skipped**.

| Gate | Verdict | Notes |
|---|---|---|
| V1 | **PASS** | 4 dimension tests; free-space; no-gen-model; ledger |
| F1-hardened | **PASS** | SQLite 3/3; Postgres 3 skipped (no DMS_LEDGER_DSN) — debt noted |
| F7 | **PASS** | PII choke-point, crypto roundtrip, RLS SQL |
| F2 | **PASS** | Chat + ledger events + migration |

## Next dispatch
1. F3 classify (intent + sentiment, local T0/T1)
2. Phase 0 deploy (docker-compose + Caddy) — parallel planning OK
3. Close F1 Postgres CI debt with DMS_LEDGER_DSN in CI
