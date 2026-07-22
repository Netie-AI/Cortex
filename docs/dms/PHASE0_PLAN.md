# Phase 0 ΓÇö Production Deploy Plan
**Status:** Planning (post Gate F6 PASS) | **Gate target:** Gate P0  
**Rule:** Planning doc only until supervisor approves scope. No parallel feature builds.

---

## Goal
One-command demo and deploy path that works on a clean machine without manual env prep:
- `.\demo\run_demo.ps1` ΓåÆ API :8000 + UI :3000 green
- `.\scripts\verify_all.ps1` ΓåÆ 19/19 checks (or documented skips only when demo not running)
- Postgres DSN wired for ledger + ops (SQLite remains valid local fallback)

---

## Current gaps

| Gap | Impact | Phase 0 fix |
|---|---|---|
| No `env.example` for DMS | Manual copy from `env.local` | Ship `demo/env.example` (no secrets) |
| `docker-compose.yml` is Qdrant-only | No prod stack | Add `deploy/docker-compose.yml` (Postgres + API + UI + Caddy) |
| `DMS_LEDGER_DSN` unset in CI | F1 Postgres tests skipped | CI service container + env in `.github/workflows/test.yml` |
| `verify_all.ps1` hard-fails without `node` | 18/19 on dev laptops | Treat node as required for full pass; document in prerequisites OR skip with WARN when absent |
| API/UI live checks skip when demo down | Expected | Gate P0 requires demo running during verify |
| F7 remainder (SOPS, rate limit) | Not blocking demo | Parallel track ΓÇö confirm before external pilot |

---

## F7 gate status (confirm before pilot)
| Item | State |
|---|---|
| PII `redact_for_prompt` choke-point | Shipped ΓÇö Gate F7 PASS |
| AES-256-GCM envelope crypto | Shipped ΓÇö tests green |
| RLS SQL migrations | Shipped ΓÇö not CI-verified without Postgres |
| SOPS+age secrets | **Debt** ΓÇö env vars still |
| Rate limiting | **Debt** ΓÇö not on API routes |

**Decision:** F7 core PASS on branch; remainder is pre-pilot hardening, not Phase 0 blocker.

---

## Architecture invariant (preserve)
Captured skills feed **F4 suggest ranking only**. They must **never** modify F5 compliance YAML rules or gate thresholds. Any proposal to let skills change gate logic ΓåÆ `PARKING_LOT.md` P14 + separate gate.

---

## Deliverables (build sequence)

### P0.1 ΓÇö Env contract
- [ ] `demo/env.example` ΓÇö `PACK`, `DMS_OPS_DB`, `DMS_LEDGER_DSN`, `DMS_SKILL_CAPTURE_ENABLED`, placeholders only
- [ ] `run_demo.ps1` loads `demo/env.example` defaults when no local override
- [ ] Document: SQLite default; Postgres when `DMS_LEDGER_DSN` set

### P0.2 ΓÇö Docker stack
- [ ] `deploy/docker-compose.yml`: `postgres`, `api`, `ui`, `caddy` (TLS optional local)
- [ ] `deploy/Caddyfile` ΓÇö reverse proxy :443 ΓåÆ api:8000, ui:3000
- [ ] Healthchecks: `/health`, `/health/db`
- [ ] Volume mounts for DuckDB sample + SQLite ops fallback

### P0.3 ΓÇö Postgres wiring
- [ ] Run `packs/dms/sql/002_ledger_postgres.sql` + `003_rls_policies.sql` + `005` + `006` on startup
- [ ] CI: Postgres service + `DMS_LEDGER_DSN` ΓåÆ F1 Postgres tests un-skipped
- [ ] `verify_all.ps1`: optional Postgres smoke when `DMS_LEDGER_DSN` set

### P0.4 ΓÇö verify_all + demo green
- [ ] Prerequisites doc: Python 3.10+, Node 18+, pip editable install
- [ ] `verify_all.ps1`: `python -m pytest tests/` (already); node check documents install link on fail
- [ ] Manual smoke: `/`, `/brain`, `/skills`, `/chat` ΓÇö verdict colors + skills banner

### P0.5 ΓÇö Gate P0 packet
- [ ] `docs/dms/GATE_P0_PACKET.md` ΓÇö compose build, TLS curl, verify_all output, demo screenshot checklist

---

## Anti-scope (Phase 0)
- No Palantir ontology, no V2/V3 vision, no RUMA pack changes
- No skill export, no default-ON capture
- No production WASM / Firecracker
- Do not block Phase 0 on F7 SOPS/rate-limit unless pilot date is set

---

## Acceptance (Gate P0)
```
docker compose -f deploy/docker-compose.yml up --build -d
curl https://localhost/health   # or http://localhost:8000/health local
.\scripts\verify_all.ps1        # 19/19 with demo running
pytest tests/ -q                # 145+ passed, 4 skipped
```

---

## Next dispatch after plan approval
Ship **P0.1** (env contract + run_demo self-bootstrap) as smallest first increment, then P0.2 docker stack.
