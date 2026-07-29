# ARCHITECTURE.md — CortexOS + DMS Brain
**Planned/partial is marked. Do not present planned as shipped.**

---

## 1. System layers

```
CLIENT     demo/dms-ui/ (Next.js 14) — query, warehouse, chat, brain, skills, audit
     │ HTTP
API        CortexOS/api/ (+ engine, memory, sidecar, lakehouse routes)
     │
PACK       packs/dms/ — vision, audit, chat, tasks, skills, lakehouse, security
     │
RUNTIME    CortexOS/ — engine registry, memory plane, compliance, ponytail, RAG (partial)
     │
DATA       DuckDB analytics | DuckLake lakehouse (L0) | SQLite ops | Postgres (target)
```

---

## 2. Built vs partial (honest)

### CortexOS runtime
| Module | State |
|---|---|
| Pack loader | Shipped |
| Compliance engine (YAML rules) | Shipped |
| Engine registry (`/api/engine/*`) | Shipped (descriptors/policy; not full serving orch) |
| Memory plane + rawknn (`/api/memory/*`) | Shipped (M0) — not wired into DMS chat UI yet |
| AirGPT sidecar (`/dms/secure|classify|audit`) | Shipped |
| DAG runner | Partial — no Temporal, limited fan-out |
| Cost ledger | Partial |
| Model routing T0–T3 | Partial |
| Hybrid RAG | Partial — not wired to demo |
| WASM sandbox | Scaffold only |
| A2A / personality | Planned (RUMA) |

### DMS pack
| Feature | State | Gate |
|---|---|---|
| F1 ledger (SQLite + Postgres DSN) | Shipped | F1-hardened PASS |
| F7 EXIF strip | Shipped | V0 |
| F7 PII/crypto/RLS SQL | Shipped (minimal) | F7 PASS |
| F7 remainder (API-key RBAC + rate limit) | Shipped (skills routes) | F7 remainder in progress |
| V0 warehouse | Shipped | V0 PASS |
| V1 dimensioning | Shipped | V1 PASS |
| F2 chat foundation | Shipped | F2 PASS |
| F3 classify (intent + PII-before-model) | Shipped | F3-security PASS |
| F4 task suggest + Ponytail + Brain | Shipped | F4 PASS |
| F5 compliance gate | Shipped | F5 PASS |
| F6 skill capture (opt-in) | Shipped | F6 PASS |
| L0 DuckLake lakehouse | Shipped | BUILD_PLAN_V2 |
| L1 ingest + L2 pipelines | Shipped | BUILD_PLAN_V2 |
| Q1 semantic + Q2 answer engine | Shipped | BUILD_PLAN_V2 (accuracy gate MET) |
| S0 streams → bronze | Shipped | BUILD_PLAN_V2 (~380 ev/s stress) |
| S1 watcher agents (detect→draft→approve) | Core shipped | DBOS resume + @agent chat open |
| U0 Data Studio | Planned | BUILD_PLAN_V2 |
| V2/V3 vision | Planned | VISION_GOVERNANCE.md |
| S2 broker / F8 tool-call | Packet / research | GATE_F8 + findings/S2_* |

### Demo
| Component | State |
|---|---|
| Query + warehouse + chat + brain + skills + audit UI | Running |
| `run_demo.ps1 -Fast` / portable SSD launchers | Running |
| Engine/memory/lakehouse APIs | Shipped — no dedicated UI pages yet |
| DuckDB 25k rows | Running |

---

## 3. Not built (do not demo as built)
- Palantir ontology / full AIP lineage (research docs only)
- Production WASM / microVM
- Postgres ledger CI verification / RLS proof (DSN optional locally) — Claude Code packet
- SOPS production secrets hygiene — Claude Code packet
- Live Qdrant RAG in demo
- `@agent` chat dispatch + DBOS durable resume (S1 remainder)
- U0 Data Studio single page
- F8 governed tool-call publish
- V2/V3 vision movement
- respond.io-style messaging endpoint
- Phase 0 production TLS deploy — see `docs/dms/PHASE0_PLAN.md`

**Truth map:** `docs/dms/TRUTH_GROUND_MAP.md` · **Claude security:** `docs/dms/packets/CLAUDE_CODE_SECURITY_PACKET.md`

---

## 4. V0 data flow (working)

```
POST /dms/warehouse/locations     → location + qr_token
GET  .../qr-label                   → PNG for printing
POST /dms/items/intake              → EXIF strip, item, ledger item.intake
POST /dms/movements/scan            → move, ledger item.moved
POST /dms/query                     → sqlglot → DuckDB
```

---

## 5. Tech stack

| Layer | Tech | State |
|---|---|---|
| API | FastAPI + uvicorn | Shipped |
| Analytics | DuckDB + sqlglot | Shipped |
| Lakehouse | DuckLake (L0) | Shipped |
| Ops DB | SQLite (demo) → Postgres | Partial — Phase 0 wires DSN |
| Frontend | Next.js 14 | Shipped |
| QR / photos | qrcode, Pillow | Shipped |
| Vector / vision | Placeholders | Planned |

---

## 6. Branch truth (2026-07-29)

**Canonical write-up:** [`docs/strategy/CORTEX_WHITEPAPER.md`](docs/strategy/CORTEX_WHITEPAPER.md) §7.

| Branch | Tip (measured) | Role |
|---|---|---|
| **`main`** | `cd66578` | Integration trunk. Governance + landed engine + DMS pack. Ahead of `origin/main` by skill-embedding fix. **Use this.** |
| `fix/deterministic-skill-embedding` | `cd66578` | Identical to `main` — already merged; delete after push. |
| `feat/context-engineering` | `be945ac` | Fully merged into `main` (context assembler + `/api/context`). Historical — delete. |
| `netie-engine` | `028fbfb` | Diverged Brain-B R&D feeder (not fully merged). Capabilities cherry-picked via path C. Do not rebase. Archive/read-only. |

Retired names (`dms-v2`, `netie-engine-up`, `dms-integrated-engine`) are historical merge parents — not active trunks.

Invariant: F6 skills feed **suggest ranking only**; F5 YAML rules govern execution.

Read `STATUS.md` before any architecture change.
