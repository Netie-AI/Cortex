# Gate F4 — Task Suggest + Ponytail + Brain
**Date:** 2026-06-26 | **Branch:** dms-v2 | **Verdict:** pending Claude review

## Scope
F4 task suggestion engine, Ponytail token-saving middleware, DMS Brain generative routes + UI.

## Test evidence
```
pytest -q → 134 passed, 4 skipped
pytest tests/dms/test_f4_task_suggest.py -q  → 12 passed
pytest tests/dms/test_ponytail.py -q         → 9 passed
pytest tests/dms/test_generative.py -q       → 12 passed
```

## Gate F4 checklist
- [ ] All task suggestions have `requires_confirm=True`
- [ ] Rule candidates: stale audit, overload rebalance, compliance flags, batch confirm
- [ ] `record_choice` / `refresh_stats` roundtrip (accept rate in stats)
- [ ] Ponytail: PII stripped before context; injection/scam flagged
- [ ] Ponytail: tier routing T0 for simple lookups, T2 for analyze/draft
- [ ] Brain: email/WhatsApp drafts always `requires_confirm=True`
- [ ] Brain: chart/CSV/analysis read-only (`requires_confirm=False`)
- [ ] Brain: PII redacted in `run()` before `_ai()` dispatch
- [ ] Every brain call writes F1 ledger event `brain.invoked`
- [ ] UI `/brain` loads; quick commands hit API routes

## Generative stress scenarios (manual)
| Scenario | Route | Governance |
|---|---|---|
| Chart inventory | POST `/dms/brain/chart` | read-only |
| Export CSV | POST `/dms/brain/export` | read-only + download |
| Email CEO | POST `/dms/brain/email` | requires_confirm |
| WhatsApp staff | POST `/dms/brain/whatsapp` | requires_confirm |
| Weekly analysis | POST `/dms/brain/analyze` | read-only |
| CEO executive | POST `/dms/brain/auto-analysis` | read-only |
| Task suggest | POST `/dms/brain/suggest` | requires_confirm on each |

## Known debt (not blocking F4)
- SOPS+age secrets; rate limiting (F7 remainder)
- Postgres ledger CI (`DMS_LEDGER_DSN`)
- Phase 0 deploy (docker-compose + Caddy)
- LLM responses mock when `ANTHROPIC_API_KEY` unset (expected)

## Next after PASS
**F5** — deterministic compliance gate on every suggested task before execution.
