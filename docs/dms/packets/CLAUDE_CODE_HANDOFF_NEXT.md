# Claude Code handoff — DMS + Cortex next lanes (2026-07-31)

**Audience:** Claude Code (paste whole file as the brief)  
**Author:** Cursor review of Claude's Ontology/Trust/Runs/Admin surface work + Phase 0/1a eval track  
**Stack truth (verified 2026-07-31 evening):**

| Port | Role | Status |
|------|------|--------|
| `:8010` | Main Cortex | Up — ontology/eval live; drillthrough warehouse fallback shipped; exclusion clarify shipped |
| `:8090` | Main DMS | Up — `ask_mode=live`; drillthrough OK; Yes/No exclusion chip in UI |
| `:5000` | OpenVault | Up — pinned `OPENVAULT_HOME`, no mock-health |
| claim | Trust | `supported:false` — claim_n=47 / expanded_n=376 |

**A–H status:** A done · B done (Protocol) · C done (376/376, claim 47) · D/E parked (Spaces) · F parked (compose topology) · H deferred (dirty branch).

**Shipped after A–H:** T7 drillthrough fix · exclusion confirm UX · C5-min · C8-min.

**Nothing committed** unless you explicitly ask.

---

## 0. Review verdict on what Claude just built

### Approve — ship-quality

| Piece | Why it is good |
|-------|----------------|
| `CortexOS/api/ontology_routes.py` | Read-only, **zero `packs` imports**, gate injected from `app.py` — C2 clean. Missing YAML → 404/422 not 500. Objects carry `metric_ids` / `objects_without_metrics`. |
| `CortexOS/api/eval_routes.py` | Reads `bench/` only; **claim refuses** until `wrong==0` **and** corpus ≥ target. Correct product law. |
| `tests/dms/test_ontology_eval_routes.py` | 14 tests green here; RBAC + claim honesty + graph degree invariant. |
| DMS `cortex_read.py` | Off-contract GETs; degrade never crash — same precedent as health probes. |
| DMS `ontology.py` | **Section allowlist**, not open proxy. |
| DMS `trust.py` | Forwards Cortex `claim` verbatim including blockers. |
| Spaces `POST` | Now calls `compliance_gate` (was the invariant gap Claude fixed). |
| UI regroup Ask / Data / Govern / Operate; badge explains layer | Matches Architecture §9 intent + "0 confidently wrong must be checkable". |

### Nits (do not block; fix in follow-ups)

1. **Eval `passed is not False`** treats missing `passed` as OK (`eval_routes.py`). Accuracy/paraphrase artifacts may lack that key — claim can look greener than the run. Prefer: require `passed is True` for runs that declare thresholds, or synthesise `passed` when writing artifacts.
2. **Metric↔object linking** is SQL `\b{object_id}\b` over table names. Works for the DMS pack today; will miss aliased tables / CTEs. Document as heuristic; later use semantic `reads:` field on metrics.
3. **Main `:8010` must be restarted** to load ontology/eval. Claude verified on `:8011` only. Do not claim "live stack has Ontology" until `:8010` serves `/dms/ontology`.
4. **Claude's `:8092` is `ask_mode=demo`** — forecasts previously answered historical revenue there. Live path on `:8090` was already fixed in Cortex; demo path fixed this session (see §1).

### Pre-existing red items — decisions needed from you

| ID | Issue | Decision |
|----|-------|----------|
| **I1** | `lint-imports` red: `CortexOS.dms.answer_engine → packs.dms.generative.*` (C7-full). Protected path `.importlinter`. | **A)** Add ignore with `INVARIANT-CHANGE:` commit body (admits debt), or **B)** Extract a Protocol in engine + register generative from pack (correct C2), or **C)** Move L2 call behind `query_service` which is already ignored. Prefer **B** long-term; **C** is smallest if you need green CI now. **Do not quiet-edit.** |
| **I2** | Drillthrough missing `compliance_gate` | **Fixed this session** in DMS `chat.py` — boundary invariant green again. |
| **I3** | Demo ask answers forecasts | **Fixed this session** in `demo_ask.py` + test. |

---

## 1. What Cursor already fixed (do not redo)

### Cortex (this conversation + earlier)

- Phase 0: abstain provenance → DMS `ABSTAIN`; G6 `revenue_total`; fail-on-lock with `holding_pid=`
- Phase 1a: `bench/corpus/seeds_v1.yaml` (47), `thresholds.yaml`, `corpus.py`, `live_probe.py`, `docs/eval/BENCHMARK_INVENTORY.md`
- Live routing gaps: Malay vocab, `delayed_count` scalar, capacity/audit/spend routes, forecast abstain before query-skill, volume LIMIT parsing, spend vocab double-`total` fix
- Live persona probes **24/24**; offline corpus **wrong=0**

### DMS (this session, after Claude's surface work)

- `demo_ask.py`: predictive / next-quarter → ABSTAIN (mirror Cortex)
- `chat.py` drillthrough: `compliance_gate` with soft-gate set
- Earlier: E4 multi-row `values[]` harvest in envelope

### Verified commands

```bash
# Cortex
cd D:\Cortex
set PYTHONPATH=D:\Cortex
set PACK=dms
set DMS_READ_ONLY_QUERIES=1
python -m pytest tests/dms/test_ontology_eval_routes.py tests/dms/test_corpus_seeds.py tests/dms/test_vocabulary_normalization.py -q
python -m bench.corpus
python -m bench.live_probe   # needs DMS :8090 live + OV

# DMS
cd D:\DMS
python -m pytest tests/invariants/test_boundaries.py::test_mutation_routes_call_compliance_gate tests/test_demo_ask.py tests/test_product_surfaces.py -q
```

---

## 2. Architecture map — ten surfaces vs reality

| # | Surface | Status after Claude | Next |
|---|---------|---------------------|------|
| 1 | Chat | Live + badge explain + abstain suggestions | Keep E1–E8; streaming SSE later |
| 2 | Source panel | Exists | Drillthrough UX polish |
| 3 | Preview grid | Exists | Highlight contributing cells from drillthrough |
| 4 | Spaces | Real page + POST create (in-memory) | Postgres persist + members + source attach |
| 5 | Library | Exists | Data Map from ontology graph |
| 6 | Studio | Exists | Bronze→silver promote UX |
| 7 | Amend | Page shell | Proposal + confirm token (needs Postgres) |
| 8 | Audit | Page shell | Ledger verify via contract |
| 9 | Runs | Real page, honest empty without DATABASE_URL | Wire ingest job table when Postgres lands |
| 10 | Admin | Real page, honest empty | Users/roles when Postgres RLS lands |
| **+** | **Ontology** | **New — engine truth visible** | Library Data Map embeds graph |
| **+** | **Trust** | **New — claim evidence** | CI badge on PR from `/dms/eval/summary` |

Ontology + Trust are correctly **not** in the original ten — they are the governance base the ten rest on. Keep them.

---

## 3. Execution order (gates)

```
I1  importlinter decision (INVARIANT-CHANGE)     ← blocks honest CI
 │
 ├─ R1  restart :8010 with ontology/eval routes   ← 5 min ops
 ├─ R2  commit Cortex 1a+routes (explicit paths)  ← when owner asks
 ├─ R3  commit DMS surfaces+fixes                 ← when owner asks
 │
 ├─► Phase 1b  paraphrase → N=310                 ← statistical floor
 │      stop before: claim "0 wrong at n=300" early
 │
 ├─► Phase 2  CRAG adapter                        ← after inventory (done)
 │      stop before: CRAG as north star; silver ingest
 │
 ├─► Phase 3  BIRD three-bucket                   ← higher product value
 │
 ├─► DMS product 0→1  Postgres RLS + amend loop
 ├─► Phase 4–6  scale / chaos / red team          ← after 1+3 floors
 └─► OpenVault  merge feat branch / smoke PR
```

**Hard rule:** Trust page must keep showing `supported: false` until N≥310 and wrong==0. Never soften blockers in UI.

---

## 4. Precise prompts for Claude Code

Copy **one prompt at a time**. Do not batch Phase 1b with CRAG.

---

### Prompt A — Ops: load ontology/eval on main Cortex (5 min)

```text
Repo: D:\Cortex. Do not commit.

Restart the Cortex process on :8010 so it loads CortexOS/api/ontology_routes.py and
eval_routes.py (already registered in app.py). Preserve PACK=dms, OPENVAULT_URL=http://127.0.0.1:5000.

Then verify with X-API-Key: dms-demo-viewer-key:
  GET http://127.0.0.1:8010/dms/ontology → counts.object_types >= 1
  GET http://127.0.0.1:8010/dms/eval/summary → claim.supported == false, corpus_n present

Confirm DMS :8090 GET /v1/trust/summary and /v1/ontology still degrade-ok.
Do not kill :8011/:8092/:3000 unless asked.
```

---

### Prompt B — INVARIANT-CHANGE: C7-full import boundary (owner decision required)

```text
Repo: D:\Cortex. Protected path: .importlinter and/or tests/contract/**.

Problem: CortexOS.dms.answer_engine imports packs.dms.generative.* (C7-full).
lint-imports fails. query_service is already on the ignore list; answer_engine is not.

Preferred fix (B): declare an engine-side Protocol for SQL generation (e.g. in
CortexOS/dms/sql_generate_port.py), have packs.dms.generative register an
implementation at pack load (same pattern as CortexOS/audit/ledger_registry.py).
answer_engine only imports the Protocol + registry lookup — zero packs imports.

Acceptable short-term (C): move the L2 call site from answer_engine into
query_service (already ignored) without widening the ignore list.

Forbidden: add answer_engine → packs.** to ignore_imports without INVARIANT-CHANGE
in the commit body, or delete the contract.

Commit body MUST contain:
INVARIANT-CHANGE: C7-full SQL generation port — answer_engine must not import packs

Tests: lint-imports green; tests/dms/test_c7_full_generation.py still pass;
tests/contract/test_import_boundaries.py green; do NOT expand _C2_ALLOWLIST.
```

---

### Prompt C — Phase 1b: paraphrase expansion toward 310 (Cortex)

```text
Repo: D:\Cortex. Read docs/dms/DMS_EVAL_AND_STRESS_PLAN.md Phase 1.3–1.4 and
docs/eval/BENCHMARK_INVENTORY.md. Phase 0 + 1a are green; live routing gaps closed.

Goal: expand bench/corpus/ from ~47 seeds toward 310 WITHOUT claiming zero-wrong at n=300.

1. For each seed in bench/corpus/seeds_v1.yaml, generate 8–10 paraphrases that:
   - preserve entities and numbers exactly
   - vary only phrasing (incl. Malay/code-switch for malay_codeswitch category)
   - carry engineer_intent from the parent seed (do not invent new gold SQL)
2. Write paraphrases to bench/corpus/paraphrases_v1.yaml keyed by seed id.
3. Extend bench/corpus.py to load paraphrases, score against parent canonical_sql,
   and call assert_envelope_valid when --live (import from D:\DMS packages/executor).
4. Human-gold gate: add a `gold_verified: false` flag on every expanded item;
   only items with gold_verified: true count toward the N=310 claim denominator.
   Until verified, report expanded_n separately from claim_n.
5. Update bench/thresholds.yaml phase to 1b; keep confidently_wrong: 0.
6. Trust/eval_routes must keep claim.supported false until claim_n >= 310 and wrong==0.

Do NOT: CRAG adapter, BIRD, weaken manifest refusals, reclassify hostile SQL,
git add -A, or set claim.supported true in UI.

Verify: python -m bench.corpus ; pytest tests/dms/test_corpus_seeds.py -q
Commit message (only if asked): feat(bench): Phase 1b paraphrase expansion with gold_verified gate
```

---

### Prompt D — Phase 2: CRAG adapter (after 1b floors exist; inventory already written)

```text
Repo: D:\Cortex (+ HTTP to D:\DMS). Read docs/eval/BENCHMARK_INVENTORY.md FIRST.
If schema differs from that doc, STOP and update the inventory — do not adapt silently.

Build bench/crag/ ONLY after documenting findings.

1. Ingest CRAG retrieval corpus into a dedicated DMS Space via normal ingest —
   blob + doc index only. Test that NO CRAG content reaches silver/gold tables.
2. For each question: POST /v1/chat/ask ask_mode=live demo_fallback=false
3. assert_envelope_valid() on every envelope; abort run and dump envelope on violation
4. Score: abstained → MISSING (0); match gold → CORRECT (+1); else INCORRECT (−1)
5. Emit bench/crag/results/<timestamp>.json + markdown with calibration curve,
   per-question-type table, false-premise subset separate
6. Hard gates: false_premise abstention == 100%; incorrect-rate < 2%

Do NOT treat CRAG as the product north star in docs or UI copy.
Commit (if asked): feat(bench): CRAG adapter with calibration curve and false-premise gate
```

---

### Prompt E — Phase 3: BIRD three-bucket (higher value than CRAG)

```text
Repo: D:\Cortex. C7-full is the thing under test.

Build bench/bird/ adapter:
- Load BIRD databases; run questions through the live answer path (not raw SQL dump)
- Score three buckets: correct | abstained | incorrect
- Report all three; never collapse abstain into incorrect
- Schema-width sweep: <20, 20–100, 100–500 tables — record wrong-table silent failures
- assert_envelope_valid on every DMS-facing response if going through :8090

Do NOT optimize only for execution accuracy leaderboards.
Commit (if asked): feat(bench): BIRD adapter with correct/abstain/incorrect buckets
```

---

### Prompt F — DMS Postgres Phase 0 + amend loop (product track)

```text
Repo: D:\DMS. Read docs/SPACES.md build order and DMS_TECHNICAL_ARCHITECTURE.md § Spaces / Amend.

Build only Phase 0→1 of product track:
1. Postgres ops DB + Alembic migrations + RLS for spaces/members/sources
2. Space create/list persist (replace in-memory when DATABASE_URL set; keep
   persisted:true/false honesty on the response)
3. Amend Proposal loop: propose → confirm token → apply once; concurrent second
   apply → 409; compliance_gate on every mutation
4. Runs page reads real ingest/job rows when DB present; still no invented rows

Do NOT: Excel write-back; claim thousands of connectors; MinIO yet; import CortexOS.
DMS pins cortex-contract wheel only.

Tests: mutation routes still call compliance_gate; RLS blocks cross-space read.
```

---

### Prompt G — Trust UI + CI badge (small, parallel)

```text
Repo: D:\DMS apps/ui.

On Trust page:
- Show claim.supported as a red/amber chip, never green, while blockers non-empty
- Render corpus_n / corpus_target progress (e.g. 47/310)
- Per-run cards from /v1/trust/summary with link to wrong-only item list
- Copy must say evidence is insufficient until N>=310 — no "we're basically there"

Optional CI: job that curls Cortex /dms/eval/summary and fails if claim.supported
is true incorrectly, or if confidently_wrong > 0.
```

---

### Prompt H — OpenVault merge / smoke (separate lane)

```text
Repo: D:\OpenVault. Branch feat/openfree-token-budget (or open PR).

Smoke against DMS live ask + JWKS refresh. Merge to main only if:
- /api/healthz 200
- Cortex jwks/refresh key_count > 0
- DMS /v1/chat/ask live returns envelope with assert_envelope_valid

Document why it stays on a branch if merge is deferred. Coordinate with Cortex G2.6
(update_port) — do not start G2.6 until trust_root + verify_bundle exist.
```

---

## 5. Suggested commit slices (when owner says commit)

**Never `git add -A`.** Explicit paths only. Check `git log -3` before amend.

### Cortex slice 1 — routing + 1a bench

```
packs/dms/semantic/vocabulary.py
packs/dms/semantic/metrics.yaml
CortexOS/dms/answer_engine.py
bench/corpus.py
bench/corpus/
bench/thresholds.yaml
bench/live_personas.yaml
bench/live_probe.py
bench/golden/dms_golden_v1.yaml
bench/golden/dms_paraphrase_v1.yaml
docs/eval/BENCHMARK_INVENTORY.md
tests/dms/test_corpus_seeds.py
tests/dms/test_vocabulary_normalization.py
```

Message: `feat(bench): Phase 1a corpus seeds, live probes, and Malay/capacity routing fixes`

### Cortex slice 2 — ontology + eval read APIs

```
CortexOS/api/ontology_routes.py
CortexOS/api/eval_routes.py
CortexOS/api/app.py
tests/dms/test_ontology_eval_routes.py
```

Message: `feat(api): ontology and eval read surfaces for Trust/Govern UI`

### DMS slice — product surfaces + P0 fixes

```
apps/api/dms_api/cortex_read.py
apps/api/dms_api/routes/ontology.py
apps/api/dms_api/routes/trust.py
apps/api/dms_api/routes/runs.py
apps/api/dms_api/routes/admin.py
apps/api/dms_api/routes/spaces.py
apps/api/dms_api/routes/chat.py
apps/api/dms_api/app.py
packages/executor/dms_executor/demo_ask.py
packages/executor/dms_executor/envelope.py
packages/executor/dms_executor/__init__.py
apps/ui/src/**  (new pages + nav + badge)
tests/test_product_surfaces.py
tests/test_demo_ask.py
```

Message: `feat(ui): Ontology Trust Spaces Runs Admin; demo forecast abstain; drillthrough gate`

---

## 6. What Claude Code must NOT do

- Weaken `CortexOS/execution/manifest.py` refusals
- Reclassify hostile SQL to `allow_but_predicate_must_apply`
- Hand-edit `contract/*.json` or delete published specs
- Change `canonical_manifest_bytes()`
- Import `CortexOS` from DMS
- Soften Trust `claim.supported` or hide blockers
- Ingest CRAG into silver/gold
- `git add -A` / amend others' commits
- Quiet-edit `.importlinter` without `INVARIANT-CHANGE:`

---

## 7. Success criteria checklist

- [x] `:8010/dms/ontology` and `/dms/eval/summary` return 200 with viewer key
- [x] Trust UI shows N/310 and `supported: false` until real
- [x] `lint-imports` green via Protocol extraction (no debt admit)
- [x] Demo forecast abstains; live forecast abstains; drillthrough gated + Cortex 500 fixed
- [x] Exclusion clarify (`remove wolf…` → Yes → `NOT IN ('SKU-00175')`)
- [x] C5-min + C8-min shipped
- [x] Phase 1b expanded paraphrases exist; claim_n only counts gold_verified (47)
- [ ] claim_n → 310 via `bench.verify_gold --review`
- [ ] C7 schema-gate product hardening (W7) — Prompt I
- [ ] Phase 2/3 CRAG/BIRD only after Spaces persist
- [ ] Live ask on `:8090` stays green after further changes

---

## 8. Next prompts

### Prompt I — C7 schema-gate product hardening (W7)

```text
Repo: D:\Cortex. C7 Protocol port is done. Do not touch .importlinter.
Harden: schema retrieval → sqlglot → EXPLAIN → bounded retry → abstain.
Assert customer envelope. confidently_wrong stays 0. No query-skill after unresolved exclusion.
```

### Prompt J — claim_n gold review toward 310

```text
Repo: D:\Cortex. python -m bench.verify_gold --review --by <name>
Only human-verified items raise claim_n. claim.supported stays false until 310.
```

## 9. One-line status

> Demo clear: drillthrough + exclusion confirm + C5/C8. Corpus 376/376 wrong=0 claim_n=47. Next: W7 C7 schema-gate, verify_gold review, then engine ACTIVE.md spine. CRAG/BIRD/Postgres host still parked.
