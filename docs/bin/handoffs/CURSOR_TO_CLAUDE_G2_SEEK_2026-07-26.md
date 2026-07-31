# Cursor → Claude handoff — build the **active** engine (G2.0 / G2.1)

**Date:** 2026-07-26  
**From:** Cursor (mechanical lane — UI + chdir done)  
**To:** Claude (judgment lane — **future active AI**)  
**Plan:** `docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md` · PARKING_LOT **P21** · north-star `CORTEX_FINAL_GOAL.md`

**STATUS: READY FOR CLAUDE BUILD — PROACTIVE-FIRST**

---

## 0. What you just finished (foundation) → what you build next (active AI)

You shipped the **intent-in / engine-works** foundation. That is necessary but still mostly
**reactive or schedule-bound** (user fires, user imports a folder). The product owner’s
next mandate is the **active** version of the AI:

> **Actively do stuff. Do not wait to be asked.**  
> If the user stays silent for an hour, the engine must still advance the bound ethical
> enterprise goal (safely, confirm-gated where needed). Inbox/react is secondary.

| Layer | Status | Role |
|-------|--------|------|
| One-sentence routines + folder→app onboarding | **SHIPPED by you** | User states intent; engine fills knobs; guesses visible |
| AirGPT Routines/Apps UI + chdir cleanup | **SHIPPED by Cursor** | Mechanical surface on settled contracts |
| **G2.0 EnterpriseGoal + G2.1 proactive seeker** | **YOUR NEXT BUILD** | Always-on goal-seeking = the active AI |

Do **not** spend this lane polishing more reactive forms. Extend the same UX law into
**agency that starts itself**.

---

## 1. Product law (carry forward unchanged into G2)

**The engine does the work; the user states the intent.**

| Before | Now (settled) |
|--------|----------------|
| Routine: name, prompt, preset, depth, interval, cost cap, timeout, predicate JSON | **One sentence** |
| App: base64 zip + raw blocked codes | **Point at a folder** → one-sentence `about` → click Dockerize |

### Built in on purpose (never regress these)

1. **Guesses are always shown, never hidden.**  
   - Routines: every draft returns `assumptions[]` in words.  
   - Apps: every record returns `about.will_do` (and `watch_out`) before approval.  
   - **G2 must do the same:** every seek / goal-bind response exposes assumptions + proposed
     next steps in plain English. Automation people can’t inspect is automation they stop
     trusting the first time it surprises them.

2. **Two things stay manual, deliberately.**  
   - **Approval** — never auto-approve apps (only checkpoint between arbitrary code and a
     networked process on the machine).  
   - **Secrets** — report file + line; never silently strip keys from someone’s code.  
   - **G2 extension:** proactive seeker may **draft/suggest** freely; anything external,
     irreversible, money-moving, or network-exposing stays confirm-gated. Proactive ≠ reckless.

### Correctness you already fixed (keep green)

- Schedule resolves to next allowed weekday **at clock time** (not `now + interval` drift).  
- Empty output → `goal_not_met` (governor counts it).  
- No proven architecture → store `auto`, **race on first run** (racing + scheduler = one loop).  
- Empty routine goal → humanize **"Tell me what to do first"** + example.  
- `at`/`on` only schedule words when a time follows (“research on competitors” survives).

---

## 2. Do not touch

| Path | Why |
|------|-----|
| `CortexOS/execution/dag_runner.py` | Parallel dirty — byte-identical |
| `CortexOS/agent_sdk/hooks.py` | Parallel dirty — byte-identical |
| `demo/dms-ui/**` | Parallel dirty — byte-identical |
| Routine composer / schedule_spec / app onboarding / humanize goal_required | Settled — reuse |
| AirGPT `index.html` / `cortex_client.py` / `clipdrop.py` cortex proxies | Cursor wired — don’t rebuild UI |

`CortexOS/api/app.py` — **additive route registration only** for `/api/goals*` and `/api/engine/seek`.

---

## 3. Settled contracts (reuse; UI already talks to them)

### Routines

| Method | Path | Contract |
|--------|------|----------|
| POST | `/api/routines/draft` | `{goal}` → `{ok, draft, suggestions}` — nothing saved |
| POST | `/api/routines` | `{goal}` enough; empty → 400 explain `goal_required` |
| * | `/api/routines*` | pause/resume/run/fire/governor |

AirGPT: draft → preview (assumptions) → Create via `/api/cortex/routines*`.

### Apps

| Method | Path | Contract |
|--------|------|----------|
| POST | `/api/apps/import-folder` | `{path}` — caps + sentence refusals |
| GET | `/api/apps` | `about` + `explained_reasons` |
| POST | `/api/apps/{id}/dockerize` | never overwrite author Dockerfile; **approval stays human** |

AirGPT Apps hub: Cortex packages section via `/api/cortex/apps*` (AirGPT port `/api/apps` untouched).

### Test hygiene (Cursor done)

Redundant `monkeypatch.chdir(tmp_path)` removed from route fixtures. **New G2 tests: DB_PATH
monkeypatch only — never chdir.**

---

## 4. Your build — active AI (G2.0 → G2.1)

Read first: `docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md` §0 (proactive-first).

### G2.0 — Bind an ethical enterprise goal

1. `CortexOS/execution/enterprise_goal.py` — schema: `statement`, `measurable_criteria[]`,
   `hard_constraints[]`, `soft_preferences[]`, `audit_required`
2. `data/engine/goals.db` + DB_PATH isolation (same pattern as routines/scoreboard)
3. `/api/goals` CRUD — humanize empty/missing fields
4. Gate TERMINATE / money-adjacent paths: predicates ∩ hard_constraints must pass;
   collapse alone never ships (`false_pass_caught` pattern from gen-cFSM)
5. F1 ledger: `goal_id` + `predicate_results`
6. Responses include **assumptions / criteria in words** (same law as routine drafts)

**Exit:** CRUD green + high-collapse / failed-ethical-predicate ≠ success.

### G2.1 — Proactive seeker (this is the active AI)

1. `POST /api/engine/seek` `{goal_id}` — **no email/chat ingress required**
2. When routine tick has headroom and nothing is due → **still seek** (“what moves \(g\) next?”)
3. Rank candidates with existing JEPA family / `scoreboard.embed_goal` toward \(g\)
   (deeper \(V(s,a,g)\) can wait for G2.2)
4. Return `{ initiative: "proactive", proposals[], assumptions[], requires_confirm }`
5. Autonomy ladder: allowlisted low-risk may execute; external/irreversible = draft only
6. **Silence litmus test (required):** N minutes, no ingress → ≥1 safe next-step emitted

**Exit:** silence litmus green; never auto-send email / never auto-approve apps from seeker.

**Out of scope this slice:** G2.3 open-set `/fire` OSR, G2.4 telemetry compress, G2.6 update/OAuth.

---

## 5. How active AI reuses your two slices

| Settled slice | Active AI use |
|---------------|----------------|
| Routine composer | Seeker may **draft** a routine from a predicted need (`assumptions` visible) — user still Creates |
| `auto` + first-run race | Proactive DAGs with no family winner store `auto` and race — same invisible loop |
| App `describe` / dockerize | Seeker may **propose** “import this folder / dockerize” as a suggestion — never auto-approve |
| Governor + `goal_not_met` | Proactive runs that produce empty output still fail honestly and count toward pause |
| humanize sentences | Every seek failure / block is a sentence a person can act on |

---

## 6. Verify

```powershell
cd D:\Cortex
$env:DMS_AUTH_DISABLED="1"; $env:PACK="dms"

# Foundation you own — must stay green
python -m pytest tests/dms/test_routine_composer.py tests/dms/test_app_onboarding.py tests/dms/test_apps_importer.py -q

# Active AI (after you build)
python -m pytest tests/dms/test_enterprise_goal.py tests/dms/test_engine_seek.py -q
python -m scripts.secrets_scan
```

Live after seek ships:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8010/api/engine/seek -ContentType 'application/json' -Body '{"goal_id":"<id>"}'
# Expect proposals + assumptions even with an empty inbox
```

---

## 7. Hand-back checklist

- [ ] `EnterpriseGoal` CRUD + ethical predicate gate on TERMINATE  
- [ ] `POST /api/engine/seek` + **silence litmus** green  
- [ ] Seek responses show **assumptions / proposals in words** (guesses never hidden)  
- [ ] No auto-approve apps; no silent secret stripping; no auto external send  
- [ ] `dag_runner.py` / `hooks.py` / `demo/dms-ui/**` untouched  
- [ ] Tests: DB_PATH monkeypatch only (no chdir)  
- [ ] STATUS.md dated **G2.0/G2.1 active seeker** block  

---

## Bottom line for Claude

You already made **intent → engine** feel inevitable for routines and apps.  
**Now make the engine start work without being asked** — goal-bound, inspectable, confirm-gated.
That is the future active version of the AI. Cursor will add Seek UI after `/api/engine/seek` exists.
