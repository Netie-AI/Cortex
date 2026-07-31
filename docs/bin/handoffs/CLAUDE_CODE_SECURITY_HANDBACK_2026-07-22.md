# Claude Code security hand-back — C-SEC-1…8 (2026-07-22)

Executed against `docs/dms/packets/CLAUDE_CODE_SECURITY_PACKET.md` on
`dms-integrated-engine @ 1f368b5`. NEVER-TOUCH files untouched; all new work is
additive and composes beside the audited choke-points. Adversarial suite green
before and after. Suite: `pytest tests -q` → **254 → 261 passed, 8 skipped**.

---

## Per-workstream results

### C-SEC-1 — `secure_reversible()` (harness ∘ TokenVault) — SHIPPED (flag-gated)
- **Property proven:** flag off ⇒ byte-identical to audited one-way gate, no vault;
  flag on ⇒ model-safe `NETIE_` tokens, local-only unmask; blocked input never
  creates a vault; the audited regex floor still one-way-redacts what a blind vault
  detector misses; ledger summary carries counts/kinds only (no plaintext).
- **Files:** `packs/dms/security/reversible.py` (new), `tests/security/test_secure_reversible.py` (8).
- **Env gate:** `DMS_REVERSIBLE_PII=1`. Default callers unchanged.
- **Still open (Cursor):** wire `secure_reversible` into `sidecar /dms/secure`, classify,
  query_service behind the flag (G2) — one call-site swap each, keep default off until owner gate.
- **Gate impact:** F7 remainder (data-protection).

### C-SEC-2 — Postgres RLS proof — DESIGN + TEST SHIPPED; CI = Cursor
- **Property proven (when DSN present):** `app.role='viewer'` cannot read steward-only
  or foreign-tenant ledger rows; `steward` sees both. **Skips (≠ pass) without DSN.**
- **Files:** `packs/dms/sql/007_rls_ledger_force.sql` (new, FORCE RLS),
  `tests/dms/test_rls_blocks_out_of_scope_read.py` (new), `docs/security/RLS_PROOF.md`.
- **Still open (Cursor B3):** land `.github/workflows/rls.yml` (sketch in RLS_PROOF.md) +
  `[postgres]`/`psycopg[binary]` install; ensure app stamps `SET app.role/app.tenant_id`
  from `api_auth.Caller` on Postgres paths.
- **Gate impact:** F7 remainder — flips to PASS when the CI job is green.

### C-SEC-3 — SOPS + secrets hygiene — SHIPPED
- **Property proven:** clean tree → 0 findings; planted `sk-`/`AKIA`/`ghp_`/PEM/master-key
  detected; demo keys ignored; `env.local`/`key.md` blocked if tracked.
- **Files:** `scripts/secrets_scan.py` (`--staged` for pre-commit), `.sops.yaml`,
  `secrets/dms.env.example.yaml` (fake values), `tests/security/test_secrets_scan.py` (4),
  `.gitignore` (secrets/*, keep *.example/*.enc).
- **Still open (Cursor B3):** add `python -m scripts.secrets_scan --staged` as a pre-commit
  hook + a CI step; document age-key setup for the team.
- **Gate impact:** F7 remainder.

### C-SEC-4 — filetype_guard in front of intake — SHIPPED
- **Property proven:** exe-bytes-as-`.csv` rejected at the ingest API (415) **before disk**;
  same via folder loader → `failed` quarantine, no bronze table; `.xlsx` container spoof
  denied; exe-as-photo → intake `ValueError` + estimate-dims 415; real csv/png pass.
- **Files:** `packs/dms/security/intake_policy.py` (new, composes `filetype_guard`),
  wired into `CortexOS/api/ingest_routes.py`, `packs/dms/ingest/loader.py`,
  `packs/dms/vision/intake.py`, `CortexOS/api/warehouse_routes.py`;
  `tests/security/test_intake_filetype_wiring.py` (5).
- **Promotion:** `filetype_guard.py` is now WIRED — treat as NEVER-TOUCH going forward;
  extend via `intake_policy.py`.
- **Gate impact:** P0 data-protection (prod-risk reduction).

### C-SEC-5 — Crypto / transport memo — REVIEW SHIPPED
- **Deliver:** `docs/security/CRYPTO_TRANSPORT_MEMO.md` — AEAD/nonce/key-separation
  accept-with-contract; `purge()` residual-risk documented; **egress allowlist gap**
  (empty `transport.py`) with a minimal default-deny design.
- **Next slice (Claude Code, not Cursor):** implement `transport.egress_allowed()` +
  `DMS_SOVEREIGN` default-deny + test; wire the Comprehend cloud path behind it.
- **Gate impact:** separate transport wave; not blocking F8.

### C-SEC-6 — WASM isolate honesty — SHIPPED (honest)
- **Property proven:** kill-switch `CORTEX_WASM_DISABLED` fail-closed; no WASI / host
  functions linked; fuel accounting wired. Adversarial-module + hard memory-cap tests
  are **explicitly skipped** with reason (needs WAT toolchain; PARKING_LOT P2).
- **Files:** `tests/security/test_wasm_honesty.py` (3 + 1 skip).
- **Gate impact:** none — documents scaffold ≠ production. Do not claim Firecracker parity.

### C-SEC-7 — Agent publish rail audit — SHIPPED
- **Property proven (adds to existing `test_s1_agents.py`):** detectors import no
  LLM/model client (pure SQL); a rejected run cannot later be approved (no publish after
  reject; no artifact written); below-bound never fires.
- **Files:** `tests/dms/test_agent_publish_rail.py` (3).
- **Design for F8 (Cursor B4):** tool-call execution must require `steward+`, run under the
  same approve gate, and log `action.tool_call` to the F1 ledger — see `GATE_F8_PACKET.md`.
- **Gate impact:** S1 governance confirmed; input to F8.

### C-SEC-8 — FastAPI `__future__` annotations sweep — RANKED LIST (Cursor applies)
Repo rule: FastAPI route modules must NOT `from __future__ import annotations` (it can
break request-model / `Depends` resolution). 10 modules currently violate. Safe to remove
on Python ≥3.10 (native `X | None`). Mechanical — **Cursor slice**, ranked by risk:

| Rank | File | Risk driver |
|---|---|---|
| 1 | `CortexOS/api/agent_routes.py` | Pydantic body models + `Depends` |
| 2 | `CortexOS/api/ingest_routes.py` | body model + `Depends` |
| 3 | `CortexOS/api/pipeline_routes.py` | body model + `Depends` |
| 4 | `CortexOS/api/stream_routes.py` | body models (`Optional[...]`) + `Depends` |
| 5 | `CortexOS/api/lakehouse_routes.py` | body model + `Depends` |
| 6 | `CortexOS/api/brain_routes.py` | body models (also needs RBAC — G7) |
| 7 | `CortexOS/api/dms_query.py` | body models |
| 8 | `CortexOS/api/search.py` | body/query params |
| 9 | `CortexOS/api/memory_routes.py` | body models (also needs auth — G8) |
| 10 | `CortexOS/api/engine_routes.py` | body models (also needs auth — G8) |

**Note:** all 10 currently pass tests (bodies are simple), so this is latent-risk
hardening, not an active break. Remove the import + verify `pytest tests -q` per file.

---

## Cursor next slices (ordered, do not parallelize dependent work)
1. **B3** — RLS CI job (C-SEC-2) + secrets-scan pre-commit/CI (C-SEC-3).
2. Wire `secure_reversible` into 3 call sites behind `DMS_REVERSIBLE_PII` (C-SEC-1/G2).
3. Mechanical `__future__` removal, rank order (C-SEC-8); fold RBAC on brain (G7) + auth
   on memory/engine (G8) while touching those files.
4. **B4** — F8 tool-call execution per `GATE_F8_PACKET.md` (uses C-SEC-7 rail).
5. **B1** — S1 DBOS durable resume per `docs/research/findings/S1_DBOS_RESUME.md`.

## Honesty ledger (PARTIAL ≠ SHIPPED)
- Reversible PII: module + tests SHIPPED; **live-path adoption PARTIAL** (flag off by default).
- RLS: proof SHIPPED; **CI-green PARTIAL** (skips without DSN).
- SOPS: scanner + pattern SHIPPED; **team age-key rollout PARTIAL**.
- filetype_guard: **SHIPPED + WIRED** (photo + ingest).
- Transport/egress: **DESIGN ONLY** — not implemented.
