# Cursor → Claude handoff — G2.2 action-value + F1 ledger wiring

**Date:** 2026-07-26  
**From:** Cursor (Seek UI wired to your G2.0/G2.1)  
**To:** Claude (judgment — deepen the active engine)  
**Depends on:** G2.0/G2.1 **SHIPPED** (`enterprise_goal.py`, `seeker.py`, silence litmus live green)

**STATUS: READY FOR CLAUDE BUILD — G2.2**

---

## 0. What just closed (do not rebuild)

| Piece | Owner | Note |
|-------|-------|------|
| Ethical `EnterpriseGoal` + non-removable baseline constraints | Claude | Constraints cannot be absent; collapse unused in `gate_action` |
| `POST /api/engine/seek` + silence litmus | Claude | 644 suite green; live probe green |
| Seek UI (bind goal → Seek now → assumptions + proposals) | Cursor | `/api/cortex/goals*`, `/api/engine/seek` proxies |
| Routines / Apps intent-in UX | Claude + Cursor UI | Settled |

**Do not touch:** `dag_runner.py`, `hooks.py`, `demo/dms-ui/**`  
**Additive only:** register new routes in `app.py` if needed.

---

## 1. Your lane — G2.2

### A) Real \(V(s,a,g)\) (replace embedding-only ranking)

Today seeker ranks with `scoreboard.embed_goal` cosine on title+why. That is the JEPA *family* proxy, not action value.

**Build:**

1. `CortexOS/execution/action_value.py` (name concrete — not `utils`)
   - Learn / store \(V(s,a,g)\) from seek outcomes + routine run outcomes
   - Start offline / tabular: `(goal_family, action_kind, source) → value` with updates from
     recorded seeks + whether user later created a routine / approved related app / metric moved
   - Fallback to cosine when cold (keep silence litmus)
2. Wire `seeker.seek` to rank by \(V\) then cosine tie-break
3. Tests: cold → cosine; after positive outcomes → preferred action rises; still `draft_only` → `auto_ok` false

**Honesty:** do not claim world-model JEPA training. Document as value table / proxy MPC cost.

### B) F1 ledger wiring (the box you flagged)

G2.0 step 5 was deferred on purpose. Land it deliberately:

1. On `create_goal` / `update_goal` / `record_seek` / `evaluate_termination` gate denials → F1 append
   with `goal_id`, `predicate_results`, `constraint_results`, `initiative`
2. Reuse DMS ledger path (`packs/dms` audit append) — same as other engine events
3. Tests: seek produces ledger rows; false_pass / constraint_violated ledgered; no PII in payload

### Out of scope (still)

- G2.3 open-set `/fire` OSR  
- G2.4 ActionEvent compress / uplink  
- G2.5 pattern→armed assist beyond what seek already drafts  
- G2.6 update port / OAuth  

---

## 2. Verify

```powershell
cd D:\Cortex
$env:DMS_AUTH_DISABLED="1"; $env:PACK="dms"
python -m pytest tests/dms/test_enterprise_goal.py tests/dms/test_engine_seek.py tests/dms/test_action_value.py -q
# plus whatever ledger test you add
python -m scripts.secrets_scan
```

Keep silence litmus green after ranking change.

---

## 3. Hand-back checklist

- [ ] \(V(s,a,g)\) ranks seek proposals; cold fallback preserved  
- [ ] F1 ledger writes for goal bind / seek / ethical gate denials  
- [ ] Foundation 644+ still green; no chdir in new tests  
- [ ] STATUS.md dated G2.2 block  
- [ ] Flag next: G2.3 OSR **or** G2.4 telemetry (owner pick)

---

## Bottom line

You made silence productive. Now make ranking **learn from outcomes** and make every
goal/seek/gate decision **audit-native** on F1 — without loosening the ethical floor or
auto-approving anything.
