# C-SEC-5 — Crypto & transport threat memo

**Status: REVIEW MEMO (design + honest gaps). No production "ready" claim.**
Scope: `packs/dms/security/crypto.py` (envelope), `token_vault.py` (seal), empty
`CortexOS/crypto/transport.py`, tier-routing egress. No custom primitives — stdlib
+ `cryptography` only.

## 1. At-rest (envelope + vault seal) — assessment

| Property | Verdict | Note |
|---|---|---|
| AES-256-GCM (authenticated) | **OK** | `crypto.encrypt_field` + `TokenVault.seal` both AEAD; tamper → auth-tag fail |
| Nonce uniqueness | **OK, document** | 12-byte `os.urandom` per op. Safe at our volume; a GCM nonce MUST NOT repeat under one key. If a caller ever pins a static key + high write rate, rotate keys well before 2³² messages. |
| AAD binding | **OK** | vault seals bind `b"netie-token-vault"`; prevents blob cross-use |
| Key/ciphertext separation | **CONTRACT** | `seal()` returns `(key, blob)` and docstring forbids co-storage. **Gap:** nothing mechanically stops a caller writing both together — C-SEC-3 SOPS + review is the control |
| Master key source | **OK, gap** | `DMS_MASTER_KEY` (32-byte b64). **Gap:** no rotation record, no KMS; SOPS is the interim custody (G5) |
| `purge()` zeroization | **HONEST LIMIT** | Python `str` is immutable; `purge()` overwrites the dict values then clears, but prior copies may persist in memory until GC. Documented residual risk — not a hard wipe. Acceptable for local-first; note for regulated tier. |

**Accept/reject:** ACCEPT the current seal API for local-first use with the
key-custody contract enforced by SOPS + code review. REJECT any path that
persists the seal key beside the ciphertext, or reuses one key across unbounded
GCM messages without a rotation counter.

## 2. Transport / egress — gap

- `CortexOS/crypto/transport.py` is **empty**. There is no in-repo TLS termination or
  egress allowlist. Today's posture relies on: (a) local-first default (no egress),
  (b) a reverse proxy (Caddy/nginx) terminating TLS 1.3 in front of uvicorn — **out of
  repo, operator-owned**.
- **Egress risk:** `pii_ner` Comprehend path (`use_cloud=True`) and any T2/T3 model
  routing can send text off-box. There is **no allowlist** gating destinations (G12/G13).

### Minimal design (do NOT claim shipped)
1. `transport.py`: a small `egress_allowed(host: str) -> bool` checked before any
   outbound model/cloud call; default-deny with an explicit allowlist from env
   (`DMS_EGRESS_ALLOW="api.anthropic.com,..."`). Fail-closed when unset in a
   "sovereign" mode flag (`DMS_SOVEREIGN=1` → deny all egress).
2. Document that TLS 1.3 + HSTS is terminated at the reverse proxy; ship a sample
   Caddyfile in `docs/` (config, not code) — no false "we do TLS" claim in-app.
3. Route the Comprehend/T3 callers through `egress_allowed` before send; PII must be
   redacted (or vault-masked) first — reuse `secure_reversible` (C-SEC-1).

## 3. Recommended sequencing
- **Now (Claude Code, small):** ship `egress_allowed()` + `DMS_SOVEREIGN` default-deny +
  unit tests; wire the Comprehend caller behind it. (Kept as a follow-up slice — this
  memo is the design gate.)
- **Owner/ops:** reverse-proxy TLS 1.3 config; KMS story for `DMS_MASTER_KEY` at pilot.
- **Do not:** implement custom ciphers, PQC (PARKING_LOT P11), or claim FIPS/bank-grade
  transport in-app.

## Hand-back to Cursor
- Property proven: at-rest AEAD + key-separation contract reviewed; egress gap documented.
- Exact next slice (Claude Code, not Cursor): implement `transport.egress_allowed` +
  `DMS_SOVEREIGN` default-deny + test; then wire `pii_ner` cloud path behind it.
- Gate impact: F7 remainder (egress/transport is a separate wave; not blocking F8).
