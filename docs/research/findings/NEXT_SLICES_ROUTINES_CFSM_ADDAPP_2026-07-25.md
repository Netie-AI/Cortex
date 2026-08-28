# Research — next Cortex engine slices (2026-07-25)

**Context:** Gates 1–4 are green in the working tree (gen-cFSM P0, race+scoreboard, routines, app package). OpenVault/Ship/Keys stay out of this lane. AWS/domain scale remains the headline attraction for the OpenVault session.

**Trace:** research only — build when explicitly asked.

---

## Slice A — AirGPT Routines / Agents window over Cortex routes

### Cortex contracts already shipped

| Method | Path | Body / notes |
|--------|------|----------------|
| POST | `/api/engine/auto` | `{goal\|prompt, predicates[], session_id?, params?, min_runs, sim_threshold, scale}` — JEPA gate → race/direct |
| GET | `/api/engine/scoreboard` | families list |
| GET | `/api/engine/scoreboard/{family}` | stats + best preset |
| GET/POST/PATCH/DELETE | `/api/routines` | CRUD |
| POST | `/api/routines/tick` | run due routines |
| POST | `/api/routines/{id}/run` | one-shot |
| POST | `/api/routines/{id}/fire` | `{external_text, source}` — untrusted wrap |
| POST | `/api/routines/{id}/pause\|resume` | governor-friendly |
| GET | `/api/routines/{id}/runs` | history |

Sources: `CortexOS/api/race_routes.py`, `CortexOS/api/routine_routes.py`.

### AirGPT today

- **Routines page exists** as UI (`showRoutinesPage` in `D:\AirGPT\index.html`; nav includes `routines`).
- ROADMAP (2026-07-24): *“Local store now; real scheduling ties to the orchestrator next.”*
- `cortex_client.py` talks Cortex on **8010** (canonical) / health + engine specs — **no** `/api/routines*` or `/api/engine/auto` helpers found in the quick scan.
- Agents nav: All Agents / catalog / research / rag / vertex / routines / apps — agent *surfaces*, not yet the racing scoreboard.

### Gaps

1. Routines UI still local-store; not wired to Cortex `/api/routines*`.
2. No Agents/Engine panel for scoreboard families or “Run via `/api/engine/auto`”.
3. Port story: AirGPT API often **8765**; Cortex engine **8010**; app packages pin `api_base=8765`. UI must not confuse these.
4. `/fire` + untrusted payload has no UI.
5. Governor pause reasons (error streak / cost cap) not surfaced.

### Build plan (8 steps)

1. Add `cortex_client.routines_*` + `engine_auto` + `scoreboard_*` soft-fail helpers (8010).
2. Replace Routines local store reads with GET `/api/routines` (keep local draft cache optional).
3. Create / edit / pause / resume / run buttons → Cortex CRUD.
4. Add “Fire webhook text” modal → POST `.../fire`.
5. Show last runs from GET `.../runs` + paused_reason / cost_today.
6. New Engine strip: scoreboard families + “Auto-route this goal” → `/api/engine/auto`.
7. Playwright/API smoke: create routine → run → see run row (Cortex TestClient already covers routes).
8. Document ports in AirGPT ROADMAP: Routines → Cortex :8010; apps → AirGPT :8765.

---

## Slice B — gen-cFSM P1 (live collapse audit in dag_runner)

### P0 already guarantees (shipped)

- `generate_ir` → `validate_ir` → `compile_ir` → `dry_run` (no execute).
- Horizon ∈ {3,5,7}, |nodes|=horizon, restricted alphabet, 100% cycle reject.
- Collapse decision table unit-tested: TERMINATE / AUDIT_FAIL / CONTINUE / REGENERATE / FORCE_AUDIT_END.
- File: `CortexOS/execution/gen_cfsm.py` + `tests/dms/test_gen_cfsm_p0.py`.

### P1 must add (per G1 doc)

| Requirement | Meaning |
|-------------|---------|
| Live collapse | After each executed node (or phase), compute `collapse_score(state, goal)` and call `route_step` |
| Predicate AUDIT | TERMINATE only if predicates pass; else AUDIT_FAIL |
| No third orchestrator | Wrap `run_dag` / EMIT path — do not invent a new runtime |
| Exit | AFP=0 on S7-class “lying” stub test |

Doc still marks **Live gen-cFSM orchestrator = Not shipped**.

### Hook point (recommended)

`dag_runner.run_dag` already has `on_event` + **step_journal resume** (content-addressed). P1 should:

1. New thin wrapper `CortexOS/execution/constrained_fsm.py` (name from G1 plan):
   - `async def run_cfsm(goal, *, horizon=5, predicates=..., router, ledger, ...) -> dict`
   - Internally: `compile_ir(generate_ir(...))` → `run_dag(..., on_event=collapse_hook)`
2. Collapse hook on `node_done`:
   - Embed partial output / goal via `scoreboard.embed_goal` (same 64-dim hash — consistent with JEPA gate) **or** cosine of state vectors passed in.
   - Call `route_step`; on TERMINATE/AUDIT_FAIL/FORCE_AUDIT → set `should_abort`.
3. EMIT node = audit checkpoint: evaluate predicates on context output; only then allow TERMINATE success.
4. Optional preset `generative_fsm` in `architecture_presets.py` → routes to `run_cfsm` through existing `run_plan` (still one spine).

### Risks

- Open-ended LLM kinds already banned from alphabet — keep that.
- Cost ceiling: abort must still ledger partial spend.
- Don’t race gen-cFSM as a fourth preset until P1 AFP=0 proven.
- Redis working memory in G1 P1 is optional; start with in-process collapse trace.

### P1 tests (suggested)

1. Compiled IR executes via `run_dag` dry adapter.
2. collapse≥τ + predicates → TERMINATE before remaining nodes.
3. collapse≥τ without predicates → AUDIT_FAIL.
4. Horizon hit → FORCE_AUDIT_END.
5. Stall k → REGENERATE (re-generate IR once, capped).
6. Cycle IR still rejected before execute.
7. Kind outside alphabet rejected.
8. Lying stub (high collapse text, empty predicates) never TERMINATE_SUCCESS.
9. Cost ceiling still raises on projected breach.
10. HTTP optional: POST `/api/engine/cfsm` dry→live flag.

---

## Slice C — Add-app importer (ship_gate on uploaded zip)

### What Gate 4 already does (`app_package.py`)

- Stack detect: docker/node/python/static.
- Deterministic `netie_app.json` + content sha256.
- Secrets scan (OpenAI/GitHub/AWS/Google/Slack/PEM/assigned secrets).
- Zip pack/unpack with zip-slip reject + content verify.
- Port assign 8800–8899; **8765 reserved** for AirGPT API.
- `ship_gate` → `draft` (needs human) or `blocked` (secrets/unknown/stress fail).
- Tests: `tests/dms/test_app_package.py` (10 cases).

### Missing for HTTP “Add app”

| Piece | Status |
|-------|--------|
| Multipart/base64 upload API | Missing (ingest uses base64 JSON pattern — reuse) |
| Persist under `data/apps/<id>/` | Missing |
| `POST .../approve` human gate | Missing (ship_gate only returns `next: human_approval`) |
| List imported apps | Missing |
| RBAC (steward+) | Missing |
| Zip bomb limits | Not explicit (size caps needed) |
| OpenVault deploy | **Out of scope** (other lane) |

### Minimal API design

```
POST /api/apps/import     {filename, content_base64} → unpack → ship_gate → store draft
GET  /api/apps            list drafts/approved/blocked
GET  /api/apps/{id}       manifest + gate report
POST /api/apps/{id}/approve   steward confirm → status=approved + assign_port
POST /api/apps/{id}/reject
```

Storage: `data/apps/{id}/tree/` + `gate.json` + `manifest`.  
Auth: steward for import/approve (mirror ingest).  
Security: max zip bytes, zip-slip (already), secrets block, unknown stack block, no auto-start on import.

### Test plan

1. Unit: ship_gate draft/blocked (exists).
2. API: clean python zip → draft.
3. API: planted secret → blocked.
4. API: zip-slip member → unsafe / reject.
5. API: approve → port assigned, 8765 never chosen.
6. API: viewer cannot approve.

---

## Recommended build order

1. **Slice C (Add-app API)** — pure Cortex, closes Gate 4 HTTP surface; no AirGPT dependency.
2. **Slice B (gen-cFSM P1)** — core engine differentiation; keep behind preset/flag.
3. **Slice A (AirGPT Routines UI)** — consumer of Gate 2–3; needs AirGPT edits + port clarity.

Or parallel: C on Cortex while A is an AirGPT-only follow-on.

---

## Explicit non-goals this lane

- D:\OpenVault Ship/Keys rebuild.
- Public AWS/domain hosting (OpenShip).
- Committing parallel-track dirty `app.py` / STATUS without owner ask.
