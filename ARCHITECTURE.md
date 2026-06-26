# ARCHITECTURE.md — CortexOS + DMS Brain
**Planned/partial is marked. Do not present planned as shipped.**

---

## 1. System layers

```
CLIENT     demo/dms-ui/ (Next.js 14) — query, warehouse, audit
     │ HTTP
API        CortexOS/api/ + warehouse_routes.py  (PACK=dms)
     │
PACK       packs/dms/ — vision, audit, rules, sql migrations
     │
RUNTIME    CortexOS/ — execution, compliance, routing, rag (mostly partial)
     │
DATA       DuckDB (analytics demo) | SQLite ops (V0 ledger+warehouse) | Postgres (target)
```

---

## 2. Built vs partial (honest)

### CortexOS runtime
| Module | State |
|---|---|
| Pack loader | Shipped |
| Compliance engine (YAML rules) | Shipped |
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
| F7 PII/crypto/RLS SQL | Shipped (minimal) | F7 PASS — SOPS + rate limit remainder |
| V0 warehouse | Shipped | V0 PASS |
| V1 dimensioning | Shipped | V1 PASS |
| F2 chat foundation | Shipped | F2 PASS |
| F3 classify (intent + PII-before-model) | Shipped | F3-security PASS |
| F4 task suggest + Ponytail + Brain | Shipped | F4 PASS |
| F5 compliance gate | Shipped | F5 PASS |
| F6 skill capture (opt-in) | Shipped | F6 PASS |
| V2/V3 vision | Planned | VISION_GOVERNANCE.md |

### Demo
| Component | State |
|---|---|
| Query + audit UI | Running |
| Warehouse UI | Running |
| run_demo.ps1 | Fixed — requires `pip install -e ".[dms,api,dev]"` |
| DuckDB 25k rows | Running |

---

## 3. Not built (do not demo as built)
- Palantir ontology / full AIP lineage
- Production WASM / microVM
- Postgres ledger CI verification (DSN optional locally — wire in Phase 0)
- Live Qdrant RAG in demo
- V2/V3 vision movement
- respond.io-style messaging endpoint
- Phase 0 production deploy (docker-compose + Caddy + TLS) — **planned, see `docs/dms/PHASE0_PLAN.md`**

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
| Ops DB | SQLite (demo) → Postgres | Partial — Phase 0 wires DSN |
| Frontend | Next.js 14 | Shipped |
| QR / photos | qrcode, Pillow | Shipped |
| Vector / vision | Placeholders | Planned |

---

## 6. vs Palantir (honest gap)
Palantir-parity is H2/H3. **Today:** governed warehouse layer that beats Excel and sits above existing WMS. Not "Palantir for SMEs" in marketing until ontology + production ledger ship.

---

## 7. Ingest pipeline (planned, P6)
`packs/dms/ingest/` after V1: file watcher → schema infer → AI-proposed cleaning rules → human approve → deterministic apply → Splink entity resolution → standard output schema → export for Claude review. See PARKING_LOT.md for GitHub refs.

---

## 8. Subagent use
| Task | Agent |
|---|---|
| Ship F/V feature | dms-feature-builder (sequential) |
| Codebase research | dms-explore or Task explore (parallel OK, read-only) |
| Gate packet | dms-claude-gate (read-only) |
| Multi-area audit | parallel explore subagents, then synthesize |

Read `STATUS.md` before any architecture change.
