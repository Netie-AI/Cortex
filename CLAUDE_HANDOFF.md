# CLAUDE_HANDOFF — Supervisor / Gate Session
**Auto-sync:** run `python scripts/handoff.py --write` after every ship or gate. Last generated: 2026-08-28T00:20:31+00:00
**Paste for Gate F7 remainder when RBAC/RLS/SOPS slice is complete.**

---

## Your role
External supervisor. Verify F7 remainder against `docs/dms/BUILD_PLAN.md` § FEATURE 7 acceptance criteria.

## Current state
| Field | Value |
|---|---|
| Branch | `dms-v2` |
| Last gates PASS | V0–V1, F1, F7-PII, F2–F6 |
| **In progress** | **F7 remainder** — RBAC slice on `/dms/skills/*` shipped |
| Next after F7 PASS | **F8** tool-call execution (`GATE_F8_PACKET.md`) |

## F7 remainder shipped (partial)
- API-key RBAC (`api_auth.py`); actor from key not request body
- Rate limiting middleware (`rate_limit.py`)
- `tests/dms/test_f7_rbac.py` — 8 tests

## F7 remainder still open
- RBAC on remaining mutating `/dms/*` routes
- Postgres RLS proof (`test_rls_blocks_out_of_scope_read`)
- SOPS + `test_no_secret_in_repo`
- Encrypt-at-rest hooks on chat ingest (if not done)

## Test snapshot
```
Not a live count. This file is a role template. Run `python -m pytest tests/ -q`
and treat STATUS.md / the last gate log as the count, not the 153-test snapshot
that used to live here.
```

## Reference
- `docs/dms/BUILD_PLAN.md` § FEATURE 7
- `docs/dms/GATE_F8_PACKET.md` (after F7)
