# P0 — Data-protection security track (NER · reversible token vault · magic-byte)

**Status (2026-07-22, Claude Code C-SEC-1..8):**
- `filetype_guard` → **SHIPPED + WIRED** in front of photo + ingest (C-SEC-4); now NEVER-TOUCH, extend via `intake_policy.py`.
- `secure_reversible()` (harness ∘ TokenVault) → **module SHIPPED, flag-gated** (`DMS_REVERSIBLE_PII`); live-callsite adoption still **PARTIAL** (default off) — C-SEC-1.
- RLS proof → **test + FORCE migration SHIPPED**; CI-green **PARTIAL** (skips without `DMS_LEDGER_DSN`) — C-SEC-2.
- SOPS + secrets scanner → **SHIPPED** (`.sops.yaml`, `scripts/secrets_scan.py`); team age-key rollout PARTIAL — C-SEC-3.
- `pii_ner` real model → **still interface-only** (default regex).
- Transport/egress allowlist → **DESIGN ONLY** (`docs/security/CRYPTO_TRANSPORT_MEMO.md`) — C-SEC-5.

Hand-back + Cursor next slices: `docs/dms/packets/CLAUDE_CODE_SECURITY_HANDBACK_2026-07-22.md`.
Read `STATUS.md` and `ARCHITECTURE.md` first. Honesty rule: planned ≠ shipped.

This doc exists so no agent re-implements what is already done. Update the status column as waves land.

---

## 1. Where things live (security-track ontology)

```
packs/dms/security/
  pii.py              # AUDITED choke-point — regex NRIC/email/card/phone (NEVER TOUCH)
  injection_guard.py  # AUDITED — prompt-injection patterns          (NEVER TOUCH)
  scam_guard.py       # AUDITED — scam patterns                      (NEVER TOUCH)
  prompt_harness.py   # AUDITED — secure_for_prompt() gate           (NEVER TOUCH)
  photo_sanitize.py   # AUDITED — EXIF/GPS strip                     (NEVER TOUCH)
  api_auth.py         # AUDITED — API-key RBAC (F7)                  (NEVER TOUCH)
  rate_limit.py       # AUDITED — token-bucket (F7)                  (NEVER TOUCH)
  # --- new this session (additive; FREE-TO-TOUCH until wired, then promote to NEVER) ---
  token_vault.py      # NEW  reversible tokenization + local unmask vault (AES-GCM seal)
  pii_ner.py          # NEW  layered detector: regex floor + Presidio/Comprehend opt-in
  filetype_guard.py   # NEW  magic-byte sniff + extension-spoof + executable block
tests/security/
  test_adversarial_prompts.py   # AUDITED regression set (data/security/adversarial_prompts.jsonl)
  test_token_vault.py           # NEW  (5 tests)
  test_pii_ner.py               # NEW  (3 tests)
  test_filetype_guard.py        # NEW  (6 tests)
```

**Rule of engagement:** the new modules must *call into* the audited files (they reuse
`pii.detect` / `pii.PiiSpan`), never rewrite them. Wiring them into `prompt_harness`
happens through a **new** composed function, behind an owner gate, with the adversarial
suite green before/after.

---

## 2. Done vs. todo (the user's security asks)

| Ask | State | Where |
|---|---|---|
| Reversible token map + unmask ("tokens stay inside, masked data leaves") | **Landed + tested** | `token_vault.py` — `TokenVault.mask/unmask/seal/purge`, `mask_text()` |
| Local NER + fallback (Presidio → AWS Comprehend), regex floor never fails open | **Interface landed; models opt-in** | `pii_ner.py` — `LayeredDetector`, `default_detector()` |
| Magic-byte check wired against spoofed/exe uploads | **Landed + tested** | `filetype_guard.py` — `sniff()`, `validate()` |
| Adversarial gate still green (no regression) | **Verified** | `tests/security/` — 7 passed / 2 skipped (WASM) |
| Wire token vault into the live `secure_for_prompt` path (reversible mode) | **TODO** | new `secure_reversible()` composing harness + vault |
| Install a real local NER model (spaCy `en_core_web_lg` or Presidio) | **TODO** | optional dep group `pii-ner`; default stays regex |
| Wire magic-byte in front of `photo_sanitize` + file-intake routes | **TODO** | `CortexOS/api/*` intake routes |
| TLS 1.3 + egress allowlist wired to tier routing | **TODO (separate wave)** | `CortexOS/crypto/transport.py` (empty), routing adapters |

---

## 3. How to run

```powershell
# 3.12 venv (3.14 lacks wheels for heavy deps)
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install cryptography pytest pyyaml pydantic toml
$env:PYTHONPATH = (Get-Location)
.\.venv\Scripts\python -m pytest tests/security -q
```

Live reversible round-trip: PII → `NETIE_<KIND>_<hex6>` tokens → model sees only tokens →
`unmask()` re-identifies locally; `audit_summary()` returns counts only, never plaintext.

---

## 4. Design notes (why it's built this way)

- **Reversibility without egress:** the token→plaintext map lives in `TokenVault` only.
  `mask()` output is safe to send; `unmask()` runs on the model's reply inside the trust
  boundary. `seal()` gives AES-256-GCM at-rest persistence when a session must survive a
  restart; the key is caller-owned and never stored beside the ciphertext.
- **Degrade-gracefully NER:** regex is the floor and always runs. Presidio/Comprehend add
  names/addresses when their deps are present; if absent they contribute nothing, so the
  gate never protects *less* than today. Comprehend egresses text → off by default.
- **Magic-byte before parse:** `validate()` blocks unrecognized bytes, executables
  (`MZ`/`ELF`/Mach-O/shebang), disallowed types, and extension/content mismatches
  (the classic `.png` that is really a PDF/ZIP).

## 5. Next wave (do not start without reading STATUS + owner gate)

1. `secure_reversible()` in a new module — compose scam→injection block, then tokenize
   via `LayeredDetector`, return `(safe_text, vault)`. Add to adversarial suite.
2. Optional dep group `pii-ner` in `pyproject.toml`; document install; keep regex default.
3. Front `photo_sanitize` + intake routes with `filetype_guard.validate`.
4. Owner: update `STATUS.md` (F7 remainder → + data-protection slice) after the gate.
