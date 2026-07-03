# CLAUDE_HANDOFF — Supervisor / Gate Session
**Auto-sync:** run `python scripts/handoff.py --write` after every ship or gate. Last generated: 2026-07-03T04:27:53+00:00
**Paste this entire file into a new Claude chat.**

---

## Your role
You are the **external supervisor**. You do NOT implement code. You:
1. Verify gate packets against `docs/dms/GATE_F5_PACKET.md`
2. PASS or FAIL with explicit checklist
3. Block F6 until Gate F5 PASS
4. Return one-paragraph "next dispatch" for Cursor

## Current gate
| Field | Value |
|---|---|
| Branch | `dms-v2` |
| Last gates PASS | V0, V1, F1-hardened, F7, F2, F3-security, F4 |
| **Gate pending** | **F5** — compliance gate on tasks |
| Next build after PASS | **F6** skill capture (consented, opt-in) |
| Demo | Live — `docs/DEMO.md`, `.\demo\run_demo.ps1 -Fast` |

## Shipped (F5 — awaiting your verdict)
- `packs/dms/compliance/dms_rules_v1.yaml` — quote, pickup, outbound verify rules
- `packs/dms/tasks/gate.py` — `check_task()`, deterministic verdict + ledger
- `packs/dms/tasks/extract.py` — T2 extract only; rules decide pass/fail
- `packs/dms/sql/005_task_events_v0.sql`
- `CortexOS/api/task_routes.py` — `/dms/tasks/gate/check`, `/choose`, `/gate/acknowledge`
- PII-before-classify fix in `packs/dms/classify/intent.py`
- UI: chat verdict banner + brain gate inline; `demo/dms-ui/lib/api.js` restored

## Test snapshot
```
pytest -q → 141 passed, 4 skipped
pytest tests/dms/test_gate.py -q → 6 passed
```

## Gate F5 checklist (verify now)
- [ ] `test_missing_field_blocks` — missing field → fail, not executable
- [ ] `test_pass_marks_executable` — complete template → pass + ledger
- [ ] `test_value_threshold_requires_human` — high value needs steward ack
- [ ] `test_verdict_deterministic` — same input 100× → identical verdict
- [ ] `test_llm_never_decides_verdict` — random extract; rules unchanged
- [ ] `test_pii_redacted_before_classify` — NRIC never reaches intent matcher
- [ ] Full suite green (141 passed, 4 skipped)
- [ ] Manual UI: task select → green/amber/red verdict in chat/brain

## Output format (mandatory)
```markdown
## Gate: F5
**Verdict:** PASS | FAIL

### Checklist
- [x] item — evidence

### Blockers (if FAIL)
- ...

### Next dispatch for Cursor
One paragraph: F6 scope, files, acceptance tests.
```

## Uncertain / needs human
- Postgres ledger CI still skip-without-DSN (not F5 blocker)
- SOPS + rate limiting deferred (F7 remainder)
- Ontology P1 parked — condition not met

## Anti-scope (do not build)
- F6 until Gate F5 PASS
- Palantir ontology, full respond.io replacement
- Anything in `PARKING_LOT.md` without condition + gate approval

## Reference docs
- Gate packet: `docs/dms/GATE_F5_PACKET.md`
- F5 spec: `docs/dms/F5_PLAN.md`
- Changelog: `CHANGELOG_DMS.md` § F5
