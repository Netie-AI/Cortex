# P0 Security Gaps — NER / TokenVault / F7 remainder

**Research only (A5).** Defensive inventory — no exploit code, no production wiring.
**Date:** 2026-07-22  
**Sources of truth:** `docs/security/P0_NER_TOKENVAULT.md`, `STATUS.md`, `CURSOR_HANDOFF.md`, `CLAUDE_HANDOFF.md`, `PARKING_LOT.md`, `packs/dms/security/*`, `tests/security/*`

**Honesty rule:** planned ≠ shipped. Modules landed + unit-tested ≠ live gate.

---

## 1. Executive verdict

| Track | State |
|---|---|
| **P0 data-protection (NER · TokenVault · magic-byte)** | **PARTIAL** — 3 additive modules + unit tests landed; **not wired** into `secure_for_prompt`, intake, or photo path |
| **F7 core (regex PII + harness + envelope crypto + RLS SQL)** | **PASS** (historical gate) |
| **F7 remainder (RLS CI + SOPS + full RBAC coverage)** | **OPEN** — blocks full F8 |
| **Live choke-point** | Still one-way `redact_for_prompt` via `secure_for_prompt` — no reversible mode |

---

## 2. Shipped controls matrix (file → capability → test)

| File | Capability | Wired into live path? | Tests |
|---|---|---|---|
| `packs/dms/security/pii.py` | Regex PII detect + one-way `redact_for_prompt` | **Yes** — harness, classify, brain params, gate, query_service | `tests/security/test_adversarial_prompts.py`, `tests/dms/test_f7_security.py` |
| `packs/dms/security/injection_guard.py` | Prompt-injection scan / sanitize / block | **Yes** — via harness | `test_adversarial_prompts.py` |
| `packs/dms/security/scam_guard.py` | Scam pattern risk | **Yes** — harness (opt-in block) | `test_adversarial_prompts.py` |
| `packs/dms/security/prompt_harness.py` | `secure_for_prompt()` bank-grade order: scam → injection → PII redact | **Yes** — classify, query_service, sidecar `/dms/secure`, pack `secure_message` | `test_adversarial_prompts.py` |
| `packs/dms/security/photo_sanitize.py` | EXIF/GPS strip | **Yes** — `packs/dms/vision/intake.py` | (vision / F7 suite; not under `tests/security/`) |
| `packs/dms/security/api_auth.py` | API-key → viewer/steward/admin; `require_role` | **Partial** — skills, ingest, streams, agents, lakehouse, pipelines, sidecar; **not** brain / memory / engine | `tests/dms/test_f7_rbac.py`, route smoke tests |
| `packs/dms/security/rate_limit.py` | Token-bucket middleware on `/dms/*` | **Yes** — `CortexOS/api/app.py` | `tests/dms/test_f7_rbac.py` |
| `packs/dms/security/crypto.py` | AES-256-GCM envelope (`DMS_MASTER_KEY`) | **Library shipped** — callers opt-in | F7 crypto tests (dms suite) |
| `packs/dms/security/token_vault.py` | Reversible `NETIE_<KIND>_<hex6>` mask/unmask; AES-GCM `seal`/`unseal`; `purge`; audit counts only | **No** — additive only | `tests/security/test_token_vault.py` (5) |
| `packs/dms/security/pii_ner.py` | `LayeredDetector`: regex floor + Presidio/Comprehend opt-in (degrade-graceful) | **No** — interface only; default = regex | `tests/security/test_pii_ner.py` (3) |
| `packs/dms/security/filetype_guard.py` | Magic-byte sniff + ext spoof + executable block | **No** — not in front of `photo_sanitize` or ingest | `tests/security/test_filetype_guard.py` (6) |
| `CortexOS/execution/wasm_isolate.py` | Fuel/memory WASM sandbox scaffold | **Scaffold only** | 2 tests skipped unless wasmtime installed |
| `packs/dms/sql/003_rls_policies.sql` (+ `001_warehouse_v0.sql`) | Postgres RLS policies | **SQL shipped; CI proof open** | Warehouse SQLite RLS helpers; Postgres path needs `DMS_LEDGER_DSN` |

**Adversarial regression (harness path):** `tests/security/test_adversarial_prompts.py` + `data/security/adversarial_prompts.jsonl` — green against current one-way gate; **does not exercise** TokenVault / NER / filetype_guard.

---

## 3. State inventory: shipped vs gaps vs parking-lot gated

### 3.1 Shipped (do not re-implement)

- Regex PII + injection + scam + unified harness
- Demo API-key RBAC + rate limit on `/dms/*` (partial route coverage)
- Envelope crypto module
- Additive TokenVault / LayeredDetector / filetype_guard **libraries + unit tests**
- RLS **SQL migrations** on disk
- AirGPT sidecar routes using `secure_message` + RBAC (`/dms/secure`, classify, audit)

### 3.2 Gaps (active / pre-pilot — not parking lot)

See §4 ranked list. Headline: **modules exist but live gate and intake do not use them**; F7 remainder RLS CI + SOPS still open.

### 3.3 Parking-lot gated (do not start without owner move-out)

| ID | Item | Condition | Relevance |
|---|---|---|---|
| **P2** | WASM / Firecracker production hardening | First enterprise client conversation | Scaffold only today; adversarial suite skips WASM without wasmtime |
| **P11** | Post-quantum crypto (ML-KEM / ML-DSA) | Regulated-industry demand | Beyond F7 AES-GCM |
| **P1** | Full Palantir ontology + AIP | Paying clients + F1–F7 hardened | Governance layer on top of F7 — not a substitute |

TLS 1.3 + egress allowlist (`CortexOS/crypto/transport.py` is **empty**) is named in P0 doc as a **separate wave** — treat as Claude Code / owner-gated crypto work, not Cursor wiring of TokenVault.

---

## 4. P0 gaps ranked (P0 / P1 / P2) with owner

Owner legend:
- **Cursor** — low-level additive wiring, tests, route deps, optional deps (exec packet B-style)
- **Claude Code** — crypto correctness, RLS proofs, SOPS design, adversarial red-team, WASM/isolate hardening, owner-gate of NEVER-TOUCH modules

| Rank | ID | Gap | Severity | Owner |
|---|---|---|---|---|
| 1 | G1 | No `secure_reversible()` — TokenVault not composed with harness; live path remains irreversible redact | **P0** | Cursor (new module + tests) → Claude Code (owner-gate + adversarial re-test before promoting) |
| 2 | G2 | TokenVault / NER **not adopted** by classify / query_service / sidecar `/dms/secure` / brain | **P0** | Cursor (call sites behind flag) → Claude Code (gate) |
| 3 | G3 | `filetype_guard.validate` **not** in front of `photo_sanitize` or `CortexOS/api/ingest_routes.py` upload | **P0** | Cursor |
| 4 | G4 | F7 remainder: **Postgres RLS CI proof** (`DMS_LEDGER_DSN` / viewer cannot read out-of-scope) | **P0** | **Claude Code** (proof design + policy review); Cursor may add CI glue after packet |
| 5 | G5 | F7 remainder: **SOPS+age** secrets hygiene; demo keys still default in `api_auth.py` / UI | **P0** | **Claude Code** |
| 6 | G6 | Real local NER not installed — no `pii-ner` optional dep group; Presidio/spaCy never active in CI | **P1** | Cursor (pyproject + docs); Claude Code (model/FP review) |
| 7 | G7 | RBAC missing on **brain** mutators (`brain_routes.py` has no `require_role`) | **P1** | Cursor |
| 8 | G8 | **memory** (`/api/memory`) and **engine** (`/api/engine`) APIs unauthenticated | **P1** | Cursor (deps) → Claude Code (threat model for Netie surfaces) |
| 9 | G9 | FastAPI gotcha: several route modules still use `from __future__ import annotations` (`agent_routes`, `dms_query`, `search`, `lakehouse_routes`, `pipeline_routes`, `stream_routes`, `ingest_routes`) despite rule documented on brain/memory/engine | **P1** | Cursor |
| 10 | G10 | Adversarial suite does not cover reversible tokens, NER names/addresses, or magic-byte intake | **P1** | Cursor (corpus + tests) → Claude Code (red-team expansion) |
| 11 | G11 | TokenVault crypto lifecycle (key custody for `seal`, process-memory purge limits, salt/session TTL) not production-reviewed | **P1** | **Claude Code** |
| 12 | G12 | Comprehend cloud path can egress text if `use_cloud=True` — policy/allowlist absent | **P2** | **Claude Code** |
| 13 | G13 | TLS 1.3 + egress allowlist / `transport.py` empty | **P2** | **Claude Code** |
| 14 | G14 | WASM isolate production hardening | **P2** (parking **P2**) | **Claude Code** only when lot opened |

### Top 10 (action order)

1. **G1** — `secure_reversible()` composition (additive module; do not edit harness in place)
2. **G2** — Wire reversible/NER path behind owner flag to live callers
3. **G3** — Magic-byte before photo sanitize + ingest decode
4. **G4** — RLS CI proof
5. **G5** — SOPS + rotate off hardcoded demo keys for non-demo
6. **G6** — Optional `pii-ner` deps + documented default=regex
7. **G7** — RBAC on brain routes
8. **G8** — Auth on memory/engine APIs
9. **G9** — Strip illegal `__future__` annotations from FastAPI route modules
10. **G10** — Extend adversarial corpus for vault/NER/filetype

---

## 5. Higher-dimension security work — Claude Code only (packet stubs)

Cursor must not invent these mid-sprint. Each stub is a future Claude Code packet.

### 5.1 Crypto review packet — TokenVault + envelope + transport

**Goal:** Prove AES-GCM usage, key separation, and at-rest seal story are bank-grade.  
**In scope:**
- `token_vault.seal` AAD/`netie-token-vault`, nonce uniqueness, key never adjacent to ciphertext on disk
- `crypto.encrypt_field` envelope vs vault seal consistency
- `purge()` limitations (Python string immutability) — document residual risk
- Empty `CortexOS/crypto/transport.py` — TLS 1.3 + egress allowlist design for tier routing  
**Out of scope:** Implementing Presidio models; UI work.  
**Exit:** Written threat model + accept/reject of current seal API; no silent key defaults.

### 5.2 RLS proof packet

**Goal:** CI-green proof that Postgres RLS blocks cross-tenant / under-privileged reads.  
**In scope:**
- Apply `001` + `003` (+ ledger migrations) under `DMS_LEDGER_DSN`
- Test: viewer cannot read steward-only / foreign `tenant_id` rows even with direct SQL session
- Align app `SET app.tenant_id` / `app.role` with `api_auth.Caller`  
**Exit:** Named test `test_rls_blocks_out_of_scope_read` unskipped in CI; gate note in STATUS.

### 5.3 SOPS + secrets hygiene packet

**Goal:** No real secrets in repo; demo keys isolated.  
**In scope:**
- `.sops.yaml` + age; encrypt `DMS_API_KEYS` / `DMS_MASTER_KEY` material
- `test_no_secret_in_repo`; rotate guidance away from committed demo strings for pilot
- Document Vault path comment for enterprise (per BUILD_PLAN F7)  
**Exit:** Env contract + CI secret scan; demo still boots with explicit demo profile.

### 5.4 Adversarial red-team review packet

**Goal:** Expand beyond current jsonl; cover reversible tokens, NER FP/FN, polyglot uploads, prompt smuggling around `NETIE_*` tokens.  
**In scope:** Corpus design, false-negative budget, owner sign-off before wiring vault into harness.  
**Exit:** Updated `adversarial_prompts.jsonl` categories + gate checklist; **no** production exploit PoCs in-repo.

### 5.5 WASM isolate hardening packet (parking P2)

**Goal:** Production sandbox when enterprise condition met.  
**In scope:** Fuel, memory, no host I/O, supply-chain of wasmtime, kill-switch `CORTEX_WASM_DISABLED`.  
**Exit:** Unskip + harden tests; PARKING_LOT P2 move-out recorded in STATUS.

---

## 6. Cross-links to apps

| Surface | Path | Security note |
|---|---|---|
| **Demo UI keys** | `demo/dms-ui/lib/api.js` — `dms-demo-{viewer,steward,admin}-key` via `X-API-Key` | Matches `api_auth._DEMO_KEYS`; fine for local demo; **not** prod — rotate via `DMS_API_KEYS` / SOPS (G5) |
| **AirGPT sidecar** | `CortexOS/api/sidecar_routes.py` → `/dms/secure`, `/dms/classify`, audit | Uses `secure_message` → **one-way** harness today; RBAC present; will need reversible mode if AirGPT must re-identify locally (G1/G2) |
| **Ingest / lakehouse** | `ingest_routes.py`, `lakehouse_routes.py` | Steward RBAC on upload; **no** magic-byte guard before write (G3); ingest still `from __future__ import annotations` (G9) |
| **Engine API** | `CortexOS/api/engine_routes.py` (`/api/engine/*`) | Documented FastAPI rule OK (no future annotations); **no API-key auth** (G8); AirGPT POSTs hardware/config |
| **Memory API** | `CortexOS/api/memory_routes.py` (`/api/memory/*`) | Same: no future annotations; **unauthenticated upsert/query** of text+vectors (G8) — PII can land in memory without harness |
| **Brain / Ponytail** | `brain_routes.py`, `CortexOS/ponytail/middleware.py` | Brain routes lack `require_role`; Ponytail uses `redact_for_prompt` only (not vault) |
| **Classify / query** | `packs/dms/classify/intent.py`, `CortexOS/dms/query_service.py` | Live `secure_for_prompt` — irreversible |

---

## 7. Truth-ground file index

| Path | Role |
|---|---|
| `docs/security/P0_NER_TOKENVAULT.md` | P0 track status — PARTIAL; next-wave list |
| `docs/research/findings/P0_SECURITY_GAPS.md` | This inventory |
| `STATUS.md` / `CURSOR_HANDOFF.md` / `CLAUDE_HANDOFF.md` | F7 remainder open (RLS CI + SOPS); F8 blocked |
| `PARKING_LOT.md` | P2 WASM, P11 PQ crypto |
| `docs/dms/BUILD_PLAN.md` § F7 | Acceptance: RBAC + RLS + SOPS + PII |
| `docs/dms/PHASE0_PLAN.md` | RLS SQL shipped / not CI-verified; SOPS debt |
| `docs/dms/CURSOR_EXEC_PACKET_2026-07-22.md` | A5 research task; B3 F7 remainder |
| `docs/dms/GATE_F8_PACKET.md` | F8 blocked on F7 remainder |
| `packs/dms/security/*.py` | Implementations (audited vs additive) |
| `tests/security/*` | Unit + adversarial (harness-only reversible gap) |
| `packs/dms/sql/003_rls_policies.sql` | RLS policies |
| `CortexOS/crypto/transport.py` | Empty — TLS/egress wave |
| `docs/ontology/TOUCH_MAP.md` / `SECURITY_TRACK_MAP.md` | **Referenced by P0/token_vault docs but not present in tree** — treat as debt; use this file + P0 doc until restored |

---

## 8. Do-not-touch list

**NEVER TOUCH (audited choke-points)** — extend via *new* modules / composed functions only; owner gate + adversarial green before/after:

- `packs/dms/security/pii.py`
- `packs/dms/security/injection_guard.py`
- `packs/dms/security/scam_guard.py`
- `packs/dms/security/prompt_harness.py` *(do not inline-edit `secure_for_prompt`; add `secure_reversible` elsewhere)*
- `packs/dms/security/photo_sanitize.py` *(call `filetype_guard` in front — do not rewrite strip logic)*
- `packs/dms/security/api_auth.py`
- `packs/dms/security/rate_limit.py`

**FREE-TO-TOUCH until wired, then promote to NEVER:**

- `token_vault.py`, `pii_ner.py`, `filetype_guard.py` (+ their tests)

**Do not start without parking-lot / owner process:**

- Production WASM/Firecracker (P2)
- Post-quantum crypto (P11)
- Full ontology/AIP (P1)
- Rewriting regex floor “to be better NER”
- Enabling Comprehend (`use_cloud=True`) without egress policy
- Shipping real customer data before F7 remainder PASS

**Anti-patterns:**

- Trust client-supplied `actor` / role on mutating routes
- Commit real API keys or master keys
- Exploit PoCs / attack payloads in-repo (defensive tests only)
- Mid-sprint edits to NEVER-TOUCH without Claude gate

---

## 9. Recommended next slices (after research)

1. **Cursor:** G3 filetype_guard on ingest + photo path (smallest prod risk reduction; no harness edit).  
2. **Cursor:** Draft `secure_reversible()` in **new** file; unit + adversarial additions; leave default callers on one-way path until gate.  
3. **Claude Code:** G4 RLS CI + G5 SOPS packets in parallel with F8 blocker.  
4. Owner updates `STATUS.md` when data-protection slice is gated — per P0 doc §5.
