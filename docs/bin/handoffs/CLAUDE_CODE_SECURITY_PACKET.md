# CLAUDE_CODE_SECURITY_PACKET — high-dimension security work

**Audience:** Claude Code (sophisticated security generation, inspection, proofs).  
**Not for:** casual feature shipping — Cursor does B3/B4/B1 sequential builds after your designs land.  
**Truth ground:** [TRUTH_GROUND_MAP.md](../TRUTH_GROUND_MAP.md) · [P0_NER_TOKENVAULT.md](../../security/P0_NER_TOKENVAULT.md) · [P0_SECURITY_GAPS.md](../../research/findings/P0_SECURITY_GAPS.md)

**Honesty:** planned ≠ shipped. NEVER TOUCH audited choke-points listed below.

---

## 1. Mission

Operate one level above Cursor builders:

1. **Inspect** security architecture end-to-end (API → harness → vault → ledger → lake).
2. **Design** minimal, correct patches for P0 gaps (compose, don't rewrite).
3. **Generate** high-assurance security code + proof tests (RLS, SOPS, crypto seals).
4. **Red-team** with adversarial fixtures — then harden until suite stays green.
5. **Hand back** a Cursor-sized implement slice (file list + acceptance) — do not dump sprawling refactors.

---

## 2. NEVER TOUCH (audited choke-points)

Rewrite = instant FAIL. Call into these; compose beside them.

| File | Why sacred |
|---|---|
| `packs/dms/security/pii.py` | Regex floor; fail-closed |
| `packs/dms/security/injection_guard.py` | Injection patterns |
| `packs/dms/security/scam_guard.py` | Scam patterns |
| `packs/dms/security/prompt_harness.py` | `secure_for_prompt()` live gate |
| `packs/dms/security/photo_sanitize.py` | EXIF/GPS strip |
| `tests/security/test_adversarial_prompts.py` | Regression set + `data/security/adversarial_prompts.jsonl` |

Additive free (until wired, then promote):  
`token_vault.py`, `pii_ner.py`, `filetype_guard.py`

---

## 3. Workstreams for Claude Code (ordered)

### C-SEC-1 — Wire reversible secure path (design + patch)

**Goal:** `secure_reversible()` composing harness + TokenVault without breaking adversarial suite.  
**Read:** `prompt_harness.py`, `token_vault.py`, P0 doc §2.  
**Deliver:**
- New function (new file or additive module) — do **not** mutate `secure_for_prompt` semantics by default
- Feature flag / env gate (`DMS_REVERSIBLE_PII=1`)
- Tests: mask → model-safe text → unmask only inside vault boundary; no plaintext in ledger payloads
- Update P0 doc status column

**Acceptance:** adversarial suite green before/after; no plaintext PII in `ledger.append` payloads for masked paths.

### C-SEC-2 — Postgres RLS proof (F7 remainder)

**Goal:** prove out-of-scope reads fail under `DMS_LEDGER_DSN`.  
**Deliver:**
- SQL policy stubs + `tests/.../test_rls_blocks_out_of_scope_read.py`
- CI job sketch (GitHub Actions service container) — Cursor can land the YAML
- Document skip-when-no-DSN behavior (must not false-green)

**Acceptance:** with DSN → assert deny; without DSN → skip with reason (not pass).

### C-SEC-3 — SOPS + secrets hygiene

**Goal:** no real keys in git; demo keys clearly labeled.  
**Deliver:**
- `.sops.yaml` pattern + encrypted example (fake values)
- Audit script: fail CI if `sk-`, `AKIA`, private PEM, or `env.local` staged
- Rotate guidance for `DMS_API_KEYS` / demo keys
- Confirm `key.md`, `env.local`, `CortexOS/AirGPT/data*` stay gitignored

**Acceptance:** dry-run scanner on clean tree = 0 hits; intentional secret fixture detected.

### C-SEC-4 — File intake choke (magic-byte)

**Goal:** `filetype_guard.validate()` in front of photo + ingest uploads.  
**Touch:** `CortexOS/api/ingest_routes.py`, photo/upload paths — additive Depends.  
**Acceptance:** spoofed exe→jpg rejected; real png/jpeg/csv allowed per policy; tests in `test_filetype_guard.py` extended.

### C-SEC-5 — Crypto / transport higher dimension

**Goal:** inspect `packs/dms/security/crypto.py`, empty `CortexOS/crypto/transport.py`, TLS/egress story.  
**Deliver:** threat model memo + minimal TLS 1.3 / egress allowlist design (no fake “production ready” claims).  
**Anti-scope:** no custom crypto primitives; use stdlib/`cryptography` only.

### C-SEC-6 — WASM isolate adversarial pass

**Goal:** review `CortexOS/execution/wasm_isolate.py` fuel sandbox; write attack cases (infinite loop, host import probe, huge alloc).  
**Deliver:** tests that document current limits honestly (scaffold ≠ production).  
**Link:** PARKING_LOT P2 gated — do not claim Firecracker parity.

### C-SEC-7 — Agent publish rail audit (S1 × F5 × F8)

**Goal:** prove nothing publishes without human approve; ledger chain complete; no LLM in detectors.  
**Read:** `packs/dms/agents/{detectors,employee,registry}.py`, `agent_routes.py`, F8 packet.  
**Deliver:** security review checklist + any missing deny tests; design for F8 tool allowlist (steward+ only).

### C-SEC-8 — FastAPI annotation footgun sweep

**Goal:** list route modules with `from __future__ import annotations` (repo rule forbids in FastAPI routes).  
**Deliver:** ranked fix list; Cursor applies mechanical patches.

---

## 4. Inspection checklist (run every Claude Code session)

```text
[ ] STATUS + TRUTH_GROUND_MAP + P0 doc read
[ ] No edits to NEVER TOUCH files
[ ] Diff is smallest that proves the security property
[ ] New tests assert deny/fail-closed, not only happy path
[ ] Adversarial suite still green
[ ] No secrets, no AirGPT data dumps, no key.md
[ ] Hand-back section names exact Cursor next slice
[ ] Honesty: mark PARTIAL vs SHIPPED in P0 / ARCHITECTURE
```

---

## 5. Hand-back template (append to every Claude Code finish)

```markdown
## Hand-back to Cursor
- Property proven: …
- Files changed: …
- Tests added: …
- Still open: …
- Exact next Cursor slice (one B* or wire-up): …
- Gate impact: F7 remainder / F8 / S1 (pick one)
```

---

## 6. Coordination with Cursor builders

| After Claude Code finishes… | Cursor starts… |
|---|---|
| C-SEC-2 RLS design + test stub | Land CI DSN job (B3) |
| C-SEC-3 SOPS pattern | Wire into repo + docs (B3) |
| C-SEC-1 `secure_reversible` | Optional UI/steward flag |
| C-SEC-7 publish rail | F8 tool-call packet (B4) then S1 DBOS (B1) |
| C-SEC-4 filetype Depends | Ingest/photo route wiring |

Do **not** parallelize dependent security + feature builds.

---

## 7. Out of scope for this packet

- Palantir ontology O6+
- respond.io full messaging
- Production Firecracker
- Autonomous agent publish
- Weakening adversarial tests to force green
