# CLAUDE_HANDOFF — Supervisor / Gate Session
**Auto-sync:** run `python scripts/handoff.py --write` after every ship or gate. Last generated: 2026-07-03T05:06:23+00:00
**Paste this entire file into a new Claude chat for Gate F6.**

---

## Your role
You are the **external supervisor**. You do NOT implement code. You:
1. Verify gate packet against `docs/dms/GATE_F6_PACKET.md`
2. PASS or FAIL with explicit checklist
3. Block F7 remainder until Gate F6 PASS
4. Return one-paragraph "next dispatch" for Cursor

## Current gate
| Field | Value |
|---|---|
| Branch | `dms-v2` |
| Last gates PASS | V0, V1, F1-hardened, F7, F2, F3-security, F4, **F5** |
| **Gate pending** | **F6** — skill capture (consented, opt-in) |
| Next build after PASS | **F7 remainder** (RBAC, Postgres RLS, rate limiting) |
| Demo | Live — `/skills` admin page new in F6 |

## Shipped (F6 — awaiting your verdict)
- `packs/dms/skills/capture.py` — opt-in capture, gate=pass + outcome=success
- `dms_skills` table + outcome columns on task events
- F4 `suggest(trigger_text=...)` skill-match boost
- API: `/dms/skills`, `/dms/skills/config`, `/dms/skills/complete`, deactivate
- UI: `/skills` — capture on/off toggle, list, deactivate
- Ledger: `skill.captured`, `skill.deactivated`

## Test snapshot
```
Run locally: `pytest -q` — **145 passed, 4 skipped, 1 warning in 15.50s**
pytest tests/dms/test_skill_capture.py -q → 4 passed
```

## Gate F6 checklist (verify now)
- [ ] `test_capture_only_on_success_and_consent`
- [ ] `test_capture_disabled_is_noop`
- [ ] `test_captured_skill_boosts_suggestion`
- [ ] `test_deactivate_skill_excluded`
- [ ] Capture default OFF; no off-box export
- [ ] Full suite green (145 passed, 4 skipped)
- [ ] Manual: `/skills` page loads; steward toggle works

## Output format (mandatory)
```markdown
## Gate: F6
**Verdict:** PASS | FAIL

### Checklist
- [x] item — evidence

### Next dispatch for Cursor
One paragraph: F7 remainder scope.
```

## Anti-scope (do not build)
- F7 remainder or F8 until Gate F6 PASS
- Palantir ontology, full respond.io replacement
- Anything in `PARKING_LOT.md` without condition + gate approval

## Reference docs
- Gate packet: `docs/dms/GATE_F6_PACKET.md`
- Changelog: `CHANGELOG_DMS.md` § F6
- F7 spec: `docs/dms/BUILD_PLAN.md` § FEATURE 7
