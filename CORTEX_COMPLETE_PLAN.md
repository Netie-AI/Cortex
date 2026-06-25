# CORTEX OS + DMS — COMPLETE BUILD & HARDENING PLAN
**Short-term → deployable at dms.netie.ai. Long-term → production-grade governed AI platform.**
**Read ARCHITECTURE.md §2 first — this plan is built on that honest inventory.**

---

## 0. The deployment answer first (you asked about hosting)

Yes — CortexOS is a FastAPI app. It only needs:
- A process running `uvicorn CortexOS.api.main:app`
- A reverse proxy (Caddy is the easiest) handling TLS
- A Postgres instance (Supabase cloud or self-hosted)
- Optionally: a Next.js build served statically or by the same proxy

**Three hosting tiers, in order of effort:**

| Tier | Setup | Cost | When to use |
|---|---|---|---|
| **TerraMaster NAS** (you have it) | Caddy + Docker Compose on-box | ~RM0/mo | Demo + first design partner. Data never leaves. Sovereign story intact. |
| **Hetzner VPS** (CAX21, 4 vCPU ARM, 8GB) | Same Docker Compose, push to cloud | ~RM50/mo | When NAS uptime/bandwidth is a concern but you're not ready for AWS |
| **Fly.io / Railway** | `fly deploy` or push-to-deploy | ~RM80–200/mo depending on usage | Easiest public URL fast, scales automatically |

**For `dms.netie.ai` today:** Caddy on your NAS, pointed at the domain via Spaceship DNS. Two files — a `docker-compose.yml` and a `Caddyfile`. That's the whole deployment. Full spec is in §4 below.

**What "deploy as an API that people access" means in practice:** your NAS or a cheap VPS runs the FastAPI server behind Caddy (TLS termination, reverse proxy). The Next.js demo UI can be served from the same machine as a static export (`next build && next export`) or as a separate `npm run start`. Clients hit `https://dms.netie.ai/api/` for the API and `https://dms.netie.ai/` for the UI. That's it. No Kubernetes, no cloud vendor lock-in, no per-seat SaaS infrastructure needed until you have 50+ concurrent users.

---

## 1. Honest current-state gaps (what "not complete" means precisely)

From ARCHITECTURE.md §2 + §6, mapped to severity:

| Gap | Severity | Blocks what |
|---|---|---|
| F1 ledger on SQLite, no concurrent-append lock | 🔴 Critical | Any real customer data |
| F7 full security (AES-GCM, RLS, PII redact, secrets) | 🔴 Critical | Any real customer data |
| `run_demo.ps1` seed crash | 🟡 Fixed (this session) | Demo |
| DAG engine: no Temporal durable execution, no parallel fan-out | 🟡 Medium | Complex multi-step workflows |
| Cost ledger: per-node writes not fully instrumented | 🟡 Medium | Cost visibility |
| Model router: JM judgment model is rules-v0, no DistilBERT | 🟡 Medium | Tier routing accuracy |
| Hybrid RAG: Qdrant + BM25 not wired to demo | 🟡 Medium | Smart search |
| WASM sandboxing: scaffold only, not production-hardened | 🟠 Low-now / High-later | Enterprise security claims |
| F2–F6 chat/task/compliance/skill loop | 🟠 Low-now | AI-assisted workflow |
| V1+ vision inference | 🟠 Low-now | The demo "magic" |
| A2A protocol | ⚪ Parked | Future |

**The two 🔴 items block every real deployment.** They are the first things built after V1.

---

## 2. The complete build sequence

Phases are ordered: each one produces something demoable or deployable. No phase is skipped.

```
NOW          PHASE 0: Fix & deploy (NAS/cloud, TLS, Caddy)
             PHASE 1: V1 dimensioning (in progress, Gate V1)
             PHASE 2: F1 hardening (Postgres ledger, serialized append)
             PHASE 3: F7 full security (AES-GCM, PII, RLS, secrets)
             PHASE 4: F2 governed chat + inbox
             PHASE 5: F3 classify (local T0/T1)
             PHASE 6: F4 task suggest + batch learning
             PHASE 7: F5 compliance gate
             PHASE 8: F6 skill capture (consented, opt-in)
             PHASE 9: DAG hardening (cost ledger, judgment model v1)
             PHASE 10: Hybrid RAG (Qdrant + BM25 + reranker)
             PHASE 11: V2 slotting prediction
3–6 MO       PHASE 12: First paying design partner live
             PHASE 13: WASM/sandbox hardening (enterprise unlock)
             PHASE 14: V3 vision movement + map
6–12 MO      PHASE 15: Palantir ontology layer (governed semantic objects)
             PHASE 16: dms.netie.ai public API + docs site
```

---

## 3. Phase-by-phase specs (short-term: Phases 0–11)

### PHASE 0 — Deploy to dms.netie.ai (do this in parallel with V1, not after)

**Goal:** a live URL, TLS, health endpoint reachable from the internet. The demo runs at a real domain before you pitch anyone.

```
Files to create:
  docker-compose.yml        (root)
  Caddyfile                 (root)
  .env.example              (root, no secrets)
  scripts/deploy.sh         (build + push + restart)

docker-compose.yml services:
  api:
    build: .
    command: uvicorn CortexOS.api.main:app --host 0.0.0.0 --port 8000
    env_file: .env
    volumes:
      - ./data:/app/data          # DuckDB + photo store on-box
    restart: unless-stopped

  ui:
    build: ./demo/dms-ui
    command: node server.js       # next start after next build
    env_file: .env
    restart: unless-stopped

  caddy:
    image: caddy:2-alpine
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
    restart: unless-stopped

Caddyfile:
  dms.netie.ai {
    reverse_proxy /api/* api:8000
    reverse_proxy /* ui:3000
  }

Spaceship DNS:
  A record: dms.netie.ai → your NAS WAN IP (or Hetzner VPS IP)
  Port-forward 80 + 443 on router to NAS (if NAS hosting)
```

**Acceptance:** `curl https://dms.netie.ai/api/health` returns `{"status":"ok","pack":"dms"}`. The warehouse UI loads at `https://dms.netie.ai/warehouse`.

**Cursor prompt:**
```
PHASE 0 — Deploy to dms.netie.ai
Rules: cortex-core.mdc + cortex-security.mdc
Planning mode first. Create docker-compose.yml, Caddyfile, .env.example,
scripts/deploy.sh as specified in ARCHITECTURE.md §0 deployment spec.
DO NOT modify any Python or test files. DO NOT expose secrets.
.env.example has placeholder values only.
Acceptance: docker-compose up builds clean; /api/health returns ok; UI loads.
Run: docker-compose up --build then curl http://localhost:8000/health
Append to CHANGELOG_DMS.md. Bring Gate P0 packet to Claude.
```

---

### PHASE 1 — V1 dimensioning (in progress — use prompt from previous message)

Gate V1 before Phases 2+.

---

### PHASE 2 — F1 hardening (Postgres ledger + concurrent-append lock)

**Goal:** the audit ledger moves from SQLite to Postgres. Every append is serialized. A corrupted chain is detectable. This is the prerequisite for real customer data.

```
Files:
  packs/dms/audit/ledger.py           UPDATE: add Postgres path, keep SQLite for tests
  packs/dms/sql/002_ledger_postgres.sql  NEW migration

Key implementation requirements:
  - Use Postgres advisory lock (pg_try_advisory_xact_lock) OR SELECT...FOR UPDATE
    on the tail row to serialize appends. Last-write-wins is NOT acceptable.
  - The computed entry_hash formula is unchanged from the spec (F1 in build plan).
  - DB trigger: REVOKE UPDATE/DELETE from app role + trigger raises on attempt.
  - verify() walks the full chain and returns broken_at on first mismatch.
  - Concurrent test: 20 parallel appends → gap-free chain, verify() returns ok.

Anti-scope: do not touch V0/V1 warehouse routes, vision modules, or any test
            that was passing before this phase.

Acceptance:
  - test_chain_append_and_verify_ok (Postgres)
  - test_tamper_detected
  - test_append_only_enforced (trigger)
  - test_concurrent_appends_consistent (20 parallel)
Full suite green.
```

---

### PHASE 3 — F7 full security hardening

**Goal:** real customer data can now be handled. PII never reaches a prompt. Data encrypted at rest. Secrets out of code. RLS enforced at DB level.

```
Files:
  packs/dms/security/pii.py           NEW — detect + redact (NRIC, phone, email, card)
  packs/dms/security/crypto.py        NEW — AES-256-GCM envelope encryption
  packs/dms/sql/003_rls_policies.sql  NEW — RLS on all tables
  .sops.yaml + age key                NEW — secrets management (SOPS+age)

Critical implementation requirements:
  1. PII choke-point: a single wrapper function redact_for_prompt(text) that ALL
     prompt-building code must call. No other path to the model. Add a test that
     fails if raw PII reaches the prompt builder.
  2. Envelope encryption: master key from secrets (never in env vars or code) wraps
     per-record data keys. AES-256-GCM (authenticated encryption).
     encrypt_field / decrypt_field for sensitive columns.
  3. RLS: enable on dms_messages, dms_threads, dms_task_events, dms_skills,
     dms_audit_ledger. A viewer-role query cannot return steward-only rows
     even via a direct Postgres connection.
  4. Secrets: move BIG_API_PLACEHOLDER creds, master encryption key, ledger signing
     key to SOPS+age. Document Vault path in comments for future enterprise client.
  5. Rate limiting: token-bucket per user + per IP on /dms/inbox and auth endpoints.
  6. Confirm sqlglot guardrail still rejects DDL/DML (don't rebuild, verify).

The PII test is the most important: test_pii_redacted_before_prompt must exist
and must fail if the choke-point is bypassed. This is the security gate.

Anti-scope: no custom crypto, no rolling auth (extend Supabase), no post-quantum.
Full suite green. Remove all # PII-GATE-F7 markers after this phase.
```

**After Phase 3: first real warehouse can start using the system.**

---

### PHASE 4 — F2 governed chat + inbox

Inbound message intake, thread management, chat pane. Every message ledger-written. See F2 prompt in `docs/dms/BUILD_PLAN.md`.

---

### PHASE 5 — F3 intent + sentiment classify (local T0/T1)

Local-only classification on every inbound message. Intent set for warehouse/logistics (check_stock, order_status, request_quote, schedule_pickup, report_issue, update_address, complaint, chit_chat, other). See F3 prompt in BUILD_PLAN.md.

---

### PHASE 6 — F4 task suggest + batch learning

Suggestion engine over history. record_choice + record_outcome. Nightly stats refresh. See F4 prompt in BUILD_PLAN.md.

---

### PHASE 7 — F5 compliance gate on tasks

Deterministic rules gate every suggested task before execution. LLM extracts; rules decide. See F5 prompt in BUILD_PLAN.md.

---

### PHASE 8 — F6 skill capture (consented, opt-in)

Successful gated chains become reusable skill cards. Capture flag default OFF. See F6 prompt in BUILD_PLAN.md.

---

### PHASE 9 — DAG hardening + cost ledger instrumentation

**Goal:** every DAG node write executes its cost ledger entry. The judgment model is upgraded from rules-v0 to a trained DistilBERT classifier. Per-workflow cost ceilings enforced.

```
Files:
  CortexOS/routing/cost_ledger.py     UPDATE: instrument all node kinds
  CortexOS/routing/judgment_model.py  UPDATE: v1 classifier (DistilBERT) behind
                                              JUDGMENT_CLASSIFIER_VERSION placeholder
  CortexOS/dag_engine/               UPDATE: enforce cost_ceiling per workflow,
                                              halt + alert on exceed

Judgment model v1 training:
  - Collect 500 labeled examples from logged decisions (after F4 is live)
  - Input: { request_text, context_size, prior_failures }
  - Output: { tier, confidence, reason }
  - Deterministic rules still trump the classifier (legal/financial → T2+, birthday → T3)
  - Keep v0 rules as fallback when classifier confidence < 0.6

Acceptance:
  - Every test DAG node writes a cost_ledger row
  - A DAG that exceeds its cost_ceiling_myr halts and returns an error
  - JM v1 accuracy > 0.78 on held-out 100-example test set
```

---

### PHASE 10 — Hybrid RAG (Qdrant + BM25 + RRF + reranker)

**Goal:** the search path is live. Smart warehouse queries use semantic + keyword retrieval, fused and reranked.

```
Components (per plan for RUMA AI Agent §3):
  CortexOS/rag/retriever_dense.py     Qdrant + EMBEDDER_MODEL (BGE-M3)
  CortexOS/rag/retriever_sparse.py    BM25 / Postgres FTS (pg_trgm)
  CortexOS/rag/fuser_rrf.py           RRF k=60, top-30
  CortexOS/rag/reranker.py            RERANKER_MODEL (BGE-reranker-v2-m3)
  CortexOS/rag/personalization.py     EMA preference vec (after 5+ interactions)

Wire to DMS: item/supplier/location descriptions indexed on ingest.
NL query → RAG pipeline → top-N → LLM synthesis (T2) → response.
Replace DuckDB-only NL path with RAG + DuckDB hybrid (RAG for context, DuckDB for exact numbers).

Acceptance:
  - NDCG@10 > 0.78 on 50 test queries (synthetic warehouse dataset)
  - p95 latency under 800ms end-to-end (dense + sparse + rerank)
  - Exact number queries still route to DuckDB (RAG does not hallucinate counts)
```

---

### PHASE 11 — V2 slotting prediction

Velocity-based slotting + bin-packing heuristic. Deterministic, not LLM. See V2 outline in `docs/dms/VISION_GOVERNANCE.md`. Gate V2 with Claude before V3.

---

## 4. Medium-term plan (Phases 12–16, 3–12 months)

### PHASE 12 — First paying design partner live
- One warehouse SME on V0+V1+F2–F6+F7 (the full loop)
- FDE does the physical setup: location tree, labels, staff training
- Charge setup fee (RM5–15k) + monthly access (RM500–2000)
- Record a case study video (the demo that sells the next one)
- **This is the only milestone that validates everything above it**

### PHASE 13 — WASM / Firecracker sandbox hardening
- Promote `wasm_modules/` from scaffold to production-hardened isolation
- Benchmark cold-start (<125ms target per Cortex v2 spec)
- Required to make the "enterprise-grade security" claim honest
- Condition: first enterprise (non-SME) client conversation begins

### PHASE 14 — V3 vision movement + map
- Object detection at dock/gate (VISION_MODEL placeholder)
- Photogrammetry/SLAM from multiple captures → zone overlay
- Anti-scope: never replaces V0 scan-on-move as fact source — vision is an assist
- Bring full scope to Claude Gate 3 before dispatching

### PHASE 15 — Palantir ontology layer
- Governed semantic objects: raw tables → named business objects (Item, Supplier, Location, Shipment) with permissions, lineage, and registered actions
- Actions are DAG triggers: "create_pick_order" is an action on an Item, not a raw SQL call
- This is the thing that makes Cortex feel like Palantir Foundry for an SME
- Requires F1–F8 + RAG all working and stable first

### PHASE 16 — dms.netie.ai public API + docs site
- OpenAPI docs auto-generated by FastAPI → publish at `dms.netie.ai/docs`
- A simple landing page at `dms.netie.ai` explaining what the API does
- API key issuance (simple: Supabase + a `api_keys` table + middleware)
- Rate limiting per API key (already built in Phase 3)
- A "request access" form → sends to you via Resend
- This is the "anyone can use and access" version you asked for

---

## 5. Safety layer — what "complete" actually means

The safety layer is not one thing. It is five layers that work together. Here is what each is, current state, and what "complete" looks like:

| Layer | Current state | Complete state | Phase |
|---|---|---|---|
| **SQL injection prevention** | ✅ sqlglot guardrail, SELECT-only, row caps | Verify still active after every DAG change | Phase 9 |
| **PII containment** | ⚠️ EXIF strip only | PII detect + redact before every LLM prompt; AES-256-GCM at rest | Phase 3 |
| **Access control** | ⚠️ RBAC in app layer | + Postgres RLS (enforced at DB level, not just app) | Phase 3 |
| **Audit trail** | ⚠️ SQLite, no concurrent lock | Postgres, serialized, tamper-evident, verify() tested | Phase 2 |
| **Secrets** | ⚠️ Env vars | SOPS+age locally; Vault path documented for enterprise | Phase 3 |
| **Compliance gate** | ✅ YAML→Python deterministic engine | + DMS-specific rulesets (task-gate rules) | Phase 7 |
| **Sandboxing** | ⚠️ WASM scaffold | Firecracker/gVisor production-hardened | Phase 13 |
| **Transport** | ⚠️ None yet | TLS 1.3 via Caddy, HSTS | Phase 0 |
| **Rate limiting** | ⚠️ None yet | Token-bucket per user + per IP | Phase 3 |

Phases 0, 2, 3 together = the minimum safety layer for real customer data.

---

## 6. Claude gate schedule (complete)

| Gate | When | What to bring |
|---|---|---|
| Gate P0 | After Phase 0 | docker-compose builds; /health live at domain |
| Gate V1 | After Phase 1 | 4 smoke tests + free-space math + no-gen-model test |
| Gate F1-hardened | After Phase 2 | Postgres chain, concurrent-append test, tamper-detect |
| Gate F7 | After Phase 3 | PII-before-prompt test; RLS cross-tenant block; secrets scan |
| Gate F2–F6 | After Phase 8 | Full loop: message → classified → suggested → gated → skill captured |
| Gate DAG | After Phase 9 | Cost ledger per node; ceiling enforcement; JM v1 accuracy |
| Gate RAG | After Phase 10 | NDCG@10 > 0.78; latency p95; exact numbers still DuckDB |
| Gate V2 | After Phase 11 | Slotting respects constraints; every decision logged + gated |
| Gate pilot | After Phase 12 | Real client using it, FDE deployed, setup fee paid |
| Gate V3 | Before dispatching V3 | Scope review with Claude — bring what you want to build |
| Gate ontology | After Phase 15 | Named objects; action registry; lineage query works |

---

## 7. Cursor subagent discipline for parallel research tasks

You asked about parallel subagents for research. Here is the rule:

**Parallel OK:** research-only subagents (no code writes). Example: two agents simultaneously researching "best open-source annotation tools" and "Splink entity resolution performance benchmarks." They produce markdown summaries, nothing else.

**Sequential only:** any subagent that writes code, runs migrations, or modifies tests. Feature builds never run in parallel — dependencies are real.

Add to `.cursor/AGENTS.md`:
```
## Parallel subagent policy
Research subagents: parallel OK. Output = markdown summary to docs/research/.
Feature subagents: sequential only. One at a time. Claude gate between each.
A research subagent NEVER creates or modifies Python, SQL, or JSX files.
```

---

## 8. The handoff block for every new session

At the top of every new Claude chat or Cursor planning run, paste:

```
[Paste CONTEXT.md here]
[Paste STATUS.md here]
Current task: [what you're working on]
Gate status: [last gate passed]
```

That is the complete handoff. Under 1500 tokens. I will have everything needed to verify or continue without re-explaining anything.
