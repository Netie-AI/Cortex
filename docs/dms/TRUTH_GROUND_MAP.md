# TRUTH_GROUND_MAP — where every layer lives and what it does

**Purpose:** single linked index so agents edit the right file, never reinvent, and never claim planned as shipped.  
**Branch truth:** `dms-integrated-engine` / `main` @ merge `79ed093` (2026-07-22).  
**Update rule:** when a feature ships, flip its State cell here + `ARCHITECTURE.md` + `STATUS.md` + `CHANGELOG_DMS.md`.

---

## 0. Read order (always)

| # | File | Role |
|---|---|---|
| 1 | [STATUS.md](../../STATUS.md) | Current gate + next moves |
| 2 | [CURSOR_HANDOFF.md](../../CURSOR_HANDOFF.md) | Builder startup |
| 3 | [CONTEXT.md](../../CONTEXT.md) | Locked decisions |
| 4 | [ARCHITECTURE.md](../../ARCHITECTURE.md) | Built vs partial honesty |
| 5 | [PARKING_LOT.md](../../PARKING_LOT.md) | Do not build from here |
| 6 | This map | Edit targets + cross-app links |
| 7 | [CURSOR_EXEC_PACKET_2026-07-22.md](CURSOR_EXEC_PACKET_2026-07-22.md) | Remaining workstreams |
| 8 | [packets/CLAUDE_CODE_SECURITY_PACKET.md](packets/CLAUDE_CODE_SECURITY_PACKET.md) | High-end security for Claude Code |

---

## 1. System topology (truth)

```
┌──────────── demo/dms-ui (Next.js) ────────────┐
│  /query  /warehouse  /chat  /brain  /skills   │
│  /audit  (+ future /studio U0)                │
└───────────────────┬───────────────────────────┘
                    │ X-API-Key + HTTP
┌───────────────────▼───────────────────────────┐
│ CortexOS/api/*_routes.py  (FastAPI)           │
│  warehouse · chat · brain · skills · tasks    │
│  lakehouse · ingest · pipeline · streams      │
│  agents · engine · memory · sidecar           │
└───────────────────┬───────────────────────────┘
                    │
┌───────────────────▼───────────────────────────┐
│ packs/dms/  (vertical)                        │
│  semantic · agents · streams · ingest         │
│  pipelines · lakehouse · security · audit     │
│  chat · tasks · skills · compliance · vision  │
└───────────────────┬───────────────────────────┘
                    │
┌───────────────────▼───────────────────────────┐
│ CortexOS/ runtime                             │
│  answer_engine · query_service · ponytail     │
│  compliance · RAG (partial) · wasm (scaffold) │
└───────────────────┬───────────────────────────┘
                    │
┌───────────────────▼───────────────────────────┐
│ DATA                                          │
│  DuckDB warehouse · DuckLake (L0)             │
│  SQLite ops/ledger · Postgres (DSN optional)  │
└───────────────────────────────────────────────┘
```

---

## 2. Feature → files → tests → state

| ID | What it does | Implementation (edit here) | API surface | Tests | State |
|---|---|---|---|---|---|
| F1 | Hash-chained audit ledger | `packs/dms/audit/ledger.py` | audit via sidecar/brain | `tests/dms/test_f1*` | **Shipped** |
| F2 | Chat threads | `packs/dms/chat/threads.py` | `CortexOS/api/chat_routes.py` | `tests/dms/test_f2_chat.py` | **Shipped** |
| F3 | Intent classify + PII-before-model | `packs/dms/classify/intent.py` | classify via sidecar | F3 suite | **Shipped** |
| F4 | Task suggest + Brain | `packs/dms/tasks/suggest.py`, `generative/brain.py` | `task_routes.py`, `brain_routes.py` | `test_f4_*` | **Shipped** |
| F5 | Compliance gate | `packs/dms/tasks/gate.py`, `packs/dms/compliance/` | task complete | gate tests | **Shipped** |
| F6 | Opt-in skill capture | `packs/dms/skills/capture.py` | `skill_routes.py` | `test_skill_capture.py` | **Shipped** |
| F7 | PII harness + RBAC + rate limit | `security/prompt_harness.py`, `api_auth.py`, `rate_limit.py` | `/dms/*` Depends | `test_f7_rbac.py`, adversarial | **Partial** (RLS/SOPS open) |
| F8 | Governed tool-call publish | *packet only* | — | — | **Packet** → [GATE_F8_PACKET.md](GATE_F8_PACKET.md) |
| L0 | DuckLake lakehouse | `packs/dms/lakehouse/{catalog,tables}.py` | `lakehouse_routes.py` | lakehouse tests | **Shipped** |
| L1 | File ingest → bronze | `packs/dms/ingest/loader.py` | `ingest_routes.py` | `test_l1_ingest.py` | **Shipped** |
| L2 | Silver pipelines | `packs/dms/pipelines/{runner,propose}.py` | `pipeline_routes.py` | `test_l2_pipelines.py` | **Shipped** |
| Q1 | Semantic metrics + certified | `packs/dms/semantic/{loader,metrics.yaml,certified_queries.yaml,values.py}` | via Q2 | `test_q1_semantic_v2.py` | **Shipped** |
| Q2 | Adaptive answer engine | `CortexOS/dms/answer_engine.py`, `query_service.py` | warehouse/query | `test_q2_answer_engine.py`, `bench/accuracy.py` | **Shipped** |
| S0 | Stream webhook → bronze | `packs/dms/streams/{buffer,registry}.py` | `stream_routes.py` | `test_s0_streams.py`, `bench/stress.py` | **Shipped** |
| S1 | Watcher agents (AI employee) | `packs/dms/agents/{detectors,employee,registry}.py` | `agent_routes.py` | `test_s1_agents.py` | **Core shipped**; DBOS/@agent open |
| S2 | Broker tier | *research* | — | — | **Research** → findings/S2_* |
| U0 | Data Studio UI | *spec* BUILD_PLAN_V2 | thin bench/pipeline GETs | — | **Next UI** |
| B0/B1 | Accuracy + stress | `bench/{accuracy,stress}.py` | — | accuracy gate | B0 shipped; B1 research |

Master plan: [BUILD_PLAN_V2_LAKEHOUSE.md](BUILD_PLAN_V2_LAKEHOUSE.md)

---

## 3. Security ontology (NEVER TOUCH vs free)

Truth: [docs/security/P0_NER_TOKENVAULT.md](../security/P0_NER_TOKENVAULT.md)

| Path | Role | Touch rule |
|---|---|---|
| `packs/dms/security/pii.py` | Regex PII floor | **NEVER TOUCH** |
| `injection_guard.py` | Prompt injection | **NEVER TOUCH** |
| `scam_guard.py` | Scam patterns | **NEVER TOUCH** |
| `prompt_harness.py` | `secure_for_prompt()` | **NEVER TOUCH** (compose beside) |
| `photo_sanitize.py` | EXIF strip | **NEVER TOUCH** |
| `api_auth.py` / `rate_limit.py` | F7 RBAC / bucket | Extend carefully |
| `token_vault.py` / `pii_ner.py` / `filetype_guard.py` | P0 additive | Free until wired → then promote |
| `tests/security/test_adversarial_prompts.py` | Regression gate | Sacred |

Gaps research: [findings/P0_SECURITY_GAPS.md](../research/findings/P0_SECURITY_GAPS.md)  
Claude high-end: [packets/CLAUDE_CODE_SECURITY_PACKET.md](packets/CLAUDE_CODE_SECURITY_PACKET.md)

---

## 4. Cross-app / sidecar links

| App / surface | How it connects | Truth files |
|---|---|---|
| Demo UI | `demo/dms-ui/` → API with `dms-demo-{viewer,steward,admin}-key` | `demo/run_demo.ps1`, `lib/profile.js` |
| Warehouse query | UI → `query_service` / `answer_engine` | `CortexOS/dms/query_service.py` |
| AirGPT sidecar | `/dms/secure\|classify\|audit` | `CortexOS/api/sidecar_routes.py` (do not commit `CortexOS/AirGPT/` data) |
| Engine registry | `/api/engine/*` | `CortexOS/api/engine_routes.py` |
| Memory plane | `/api/memory/*` | `CortexOS/api/memory_routes.py` |
| Agents | `/dms/agents/*` | `agent_routes.py` ↔ `packs/dms/agents/*` |
| Streams | `/dms/streams/*` | `stream_routes.py` ↔ `packs/dms/streams/*` |
| Ponytail | Token discipline middleware | [docs/PONYTAIL.md](../PONYTAIL.md) |

---

## 5. Research findings index (Workstream A)

| ID | File | Feeds implementation |
|---|---|---|
| A1 | [S1_DBOS_RESUME.md](../research/findings/S1_DBOS_RESUME.md) | B1 DBOS resume |
| A2 | [S2_BROKER_SHORTLIST.md](../research/findings/S2_BROKER_SHORTLIST.md) | S2 later |
| A3 | [B1_STRESS_SUITE.md](../research/findings/B1_STRESS_SUITE.md) | B6 stress code |
| A4 | [S1_TOKEN_BUDGET.md](../research/findings/S1_TOKEN_BUDGET.md) | S1 meters + Ponytail |
| A5 | [P0_SECURITY_GAPS.md](../research/findings/P0_SECURITY_GAPS.md) | F7 remainder + Claude Code |
| IDX | [P0_INDEX.md](../research/findings/P0_INDEX.md) | Research catalog |

---

## 6. Who edits what (orchestration)

| Agent | Owns | Must not |
|---|---|---|
| **Orchestrator (this chat)** | Plan, merge, truth maps, packets, high-quality glue | Parallel feature builds |
| **Cursor builder** | One B* feature at a time from exec packet | Security rewrites of NEVER TOUCH |
| **Research explore** | `docs/research/findings/*.md` only | Python / JSX / SQL |
| **Claude Code** | High-end security: RLS proofs, SOPS, crypto review, adversarial depth, WASM isolate, `secure_reversible()` design | Casual refactors; demo data commits |
| **Claude gate** | PASS/FAIL after milestones | Implement features |

---

## 7. Rename / organize conventions

- Features: `F#` gates, `L#` lakehouse, `Q#` query, `S#` stream/agent, `U#` UI, `B#` bench
- Findings: `docs/research/findings/<TOPIC>_<YEAR>.md` or `<TRACK>_<NAME>.md`
- Packets: `docs/dms/packets/<AUDIENCE>_<TOPIC>.md`
- Never duplicate logic under `npm/` — canonical paths only
- Placeholders only: `BIG_API_PLACEHOLDER`, `EMBEDDER_MODEL`, `VISION_MODEL`, `COMPLIANCE_RULE_VERSION`

---

## 8. Verify commands

```powershell
python -m pytest tests/ -q
python -m bench.accuracy
python -m bench.stress --scenario stream --threads 8 --iterations 15
.\demo\run_demo.ps1 -Fast
python scripts/handoff.py --write
```
