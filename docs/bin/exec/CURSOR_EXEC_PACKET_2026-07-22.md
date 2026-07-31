# Cursor execution packet — 2026-07-22

**Audience:** Cursor builder agents (low-level implementation).  
**Orchestrator keeps:** high-level planning, merge/sync, S1 quality, stress re-verify.  
**Do not invent scope** — execute one numbered workstream at a time; `pytest -q` after each; append `CHANGELOG_DMS.md`.

---

## Already done (do not redo)

| Item | Status |
|---|---|
| Wave1 pull on `dms-integrated-engine` (Q1/Q2/L1/L2/S0/S1-core) | Pushed `4fb30ee` |
| S1 smoke tests `tests/dms/test_s1_agents.py` | Landed this turn |
| Stream stress re-run | ~379 ev/s, 0 errors; buffer lock split holds |
| Q2 delayed-shipments legacy bridge + abstain copy | Landed this turn |
| Branch sync pull of `origin/dms-integrated-engine` | Done |

---

## Workstream A — Research (read-only, parallel OK)

Spawn parallel `explore` / research subagents. Output markdown under `docs/research/findings/`. **No Python/JSX edits.**

1. **A1 — DBOS vs Temporal for S1 resume**  
   Confirm DBOS Transact SQLite→Postgres path, crash-mid-workflow semantics, Windows install. Write `docs/research/findings/S1_DBOS_RESUME.md` with verdict + install pins.

2. **A2 — Stream broker shortlist (S2)**  
   NATS JetStream Windows single-binary vs Redpanda/WSL2. Landing contract must match S0 bronze. Write `docs/research/findings/S2_BROKER_SHORTLIST.md`.

3. **A3 — Full stress suite design (B1)**  
   Spec k6 scripts for `/dms/query` + `/dms/streams`, chaos-lite taskkill, 24h soak profile, DuckDB concurrency knee. Write `docs/research/findings/B1_STRESS_SUITE.md` (no code yet).

4. **A4 — Token / cost budget for agent runs**  
   Ponytail-aligned: detector=SQL-only (0 LLM), draft=template±Q2, publish=F8 only after approve. Write `docs/research/findings/S1_TOKEN_BUDGET.md`.

5. **A5 — NER / TokenVault P0 hardening**  
   Review `docs/security/P0_NER_TOKENVAULT.md` + existing `pii_ner.py` / `token_vault.py`; list remaining P0 gaps vs production. Write `docs/research/findings/P0_SECURITY_GAPS.md`.

---

## Workstream B — Low-level implementation (sequential)

### B1 — S1 remainder: DBOS durable resume
**Spec:** `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md` § FEATURE S1 items 1 + 3 (resume).  
**Build:**
- Add `[agents]` extra with `dbos` pin after A1 verdict
- Persist employee workflow steps; `taskkill`-style interrupt → rerun resumes, no duplicate publish
- Unskip `test_workflow_resume_after_kill` in `tests/dms/test_s1_agents.py`
**Anti-scope:** no Temporal; no autonomous publish.

### B2 — S1 remainder: `@agent` chat dispatch
**Prereq:** F2 threads exist.  
**Build:** parse `@agent` in chat → create/inspect/run; agent replies labelled non-human.  
Unskip `test_agent_chat_dispatch`.

### B3 — F7 remainder (gate blocker for F8)
**Build exactly:**
- Postgres RLS proof test (`DMS_LEDGER_DSN` / CI)
- SOPS + secrets hygiene (no real keys committed)
- Extend API-key RBAC beyond skills to task/brain/audit mutators where still open
**Packet:** existing F7 remainder notes in `CURSOR_HANDOFF.md` / BUILD_PLAN.

### B4 — F8 tool-call execution
**Build exactly:** `docs/dms/GATE_F8_PACKET.md`  
Required so S1 `approve_run` can publish via governed tools (not just write `report.md`).

### B5 — U0 Data Studio
**Spec:** BUILD_PLAN_V2 § FEATURE U0  
Six tabs on `demo/dms-ui/app/studio/page.jsx` wired to real APIs (catalog/pipelines/quality/agents/benchmarks/audit).

### B6 — B1 stress suite (code)
After A3 research: extend `bench/stress.py` + optional `bench/k6/`; chaos-lite; write results JSON; Studio BENCHMARKS tab can read later.

### B7 — Governed metric for delayed ranking
Replace the narrow Q2 legacy bridge in `query_service.answer_question` with a real `delayed_shipments_ranked` metric (limit slot, days_delayed ORDER BY). Keep accuracy gate at 100%.

---

## Workstream C — Branch / merge hygiene (orchestrator + Cursor)

1. Keep shipping on `dms-integrated-engine` (this is the integration line).
2. After each ship: `pytest -q` → CHANGELOG → STATUS → `python scripts/handoff.py --write` → `git push origin HEAD`.
3. Open/update PR: `dms-integrated-engine` → `main` (squash or merge commit; do not force-push main).
4. Fold stale tips carefully:
   - `origin/netie-engine-wave1-wip` content already pulled into integrated via `4fb30ee`
   - Local `netie-engine-wave1-wip` has checkpoint `028fbfb` — discard or cherry-pick only if unique
   - Do **not** commit `CortexOS/AirGPT/` demo data, `outputs/`, secrets, `key.md`, `env.local`
5. `main` is behind `origin/main` by README formatting commit — merge `origin/main` into integrated before PR.

---

## Stress / verification commands (run after B* ships)

```powershell
python -m pytest tests/ -q
python -m bench.accuracy
python -m bench.stress --scenario stream --threads 8 --iterations 15
python -m bench.stress --scenario all --threads 8 --iterations 25
.\demo\run_demo.ps1 -Fast
```

**Pass bars:** pytest green; accuracy wrong=0 all tiers; stream stress errors=0 and ≥200 ev/s; demo QUERY/CHAT/BRAIN/SKILLS load.

---

## Priority order if forced serial

`C5 merge origin/main` → `B3 F7 remainder` → `B4 F8` → `B1 DBOS` → `B2 @agent` → `B5 U0` → `B7 metric` → `B6 stress suite`  
Research A1–A5 may run in parallel anytime.

---

## Explicit non-goals this packet

- No Palantir ontology O6+ (P1 gated)
- No respond.io full (P4/P9 gated)
- No committing AirGPT runtime data / binaries under `CortexOS/AirGPT/`
- No weakening existing tests to force green
