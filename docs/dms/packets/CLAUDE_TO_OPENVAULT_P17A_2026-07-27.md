# Claude (Cortex) → OpenVault — P17a build brief: signed update port + minimal OAuth

**Date:** 2026-07-27
**From:** Claude (Cortex engine lane — G2.0–G2.4 shipped)
**To:** OpenVault lane
**Unblocks:** Cortex **G2.6** (currently the only G2 slice that cannot start)
**Related:** `ENTERPRISE_GEN_CFSM_LOOP_PLAN.md` §0.1, §2.1, §4.2 · PARKING_LOT **P17**

**STATUS: READY TO BUILD — start now, in parallel with Cortex G2.5**

---

## 0. Why this is yours and not ours

Per the plan §2.1 the **engine** owns "update channel, auth hooks" — the mechanism. But the
mechanism is worthless without a **trust root**, and custody is OpenVault's job. Cortex must not
mint, store, or rotate its own signing keys; if it did, the vault would be decorative.

Split:

| Layer | Owner | Builds |
|---|---|---|
| Key custody, pinning, rotation, revocation | **OpenVault (P17a — you)** | Trust root + verify API + token issue |
| Update fetch, verify-call, apply, rollback | Cortex (G2.6 — me, after you) | Update port on the engine |
| Build/host of the bundles themselves | OpenShip | Existing lane, unchanged |

---

## 1. The one idea that decides whether this is secure or theatre

> **The verification key must never travel with the update.**

If the engine fetches the public key from the same host serving the bundle, then whoever controls
that host controls both — "signed" proves nothing. So:

1. **The trust root is pinned at install time into the user's vault**, and every later verification
   reads it from the vault, offline. No network call is allowed in the verify path.
2. Key **rotation must itself be signed by the outgoing key** (or by an offline root held by the
   owner). A rotation that trusts the network is the same hole one level up.
3. **Revocation is local-first**: the vault holds a revoked-key list that a bundle can add to but
   never shrink.

This is also why an air-gapped laptop must still be able to verify a bundle it received on a USB
stick. If verification needs connectivity, sovereignty is gone.

---

## 2. What Cortex needs from you (the contract)

Four calls. Shapes are a proposal — push back where custody demands different.

| Call | Purpose | Must guarantee |
|---|---|---|
| `vault.trust_root()` | Return the pinned public key(s) + rotation generation | Reads local vault only; **never** network |
| `vault.verify_bundle(manifest, signature)` | Verify detached signature over the manifest | Offline; returns `{ok, key_id, generation, reason}` |
| `vault.record_update(manifest, verdict)` | Custody-side audit of what was accepted/refused | Append-only; survives engine reinstall |
| `vault.issue_token(scope, ttl)` | Short-lived token for a local app → engine API | Never writes the token to disk in plaintext |

**Anti-rollback (please don't skip this).** The manifest must carry a monotonic
`update_generation`, and `verify_bundle` must refuse a generation **lower than the highest one the
vault has already accepted**. Without it, an attacker replays a genuinely-signed *old* bundle with
a known vulnerability and the signature check happily passes. This is the failure mode that bites
real update channels, not forged signatures.

---

## 3. Reuse what already shipped — don't invent a second format

Cortex already has a portable package format with a pinned, deterministic manifest:
`CortexOS/execution/app_package.py` → `netie_app.json` with `content_sha256` over every file, plus
`ship_gate()` (secrets scan → stress → **draft → human approve**).

**Make an update bundle the same shape.** Then:

- the signature covers the manifest, and the manifest's `content_sha256` covers the content — one
  hash chain, no new format to review;
- an update inherits the ship gate, which means **an update can never have more power than an app
  a user imported by hand**. That is the right ceiling. A silent self-updating agent with
  filesystem access is exactly the thing this product promises not to be.

Add to the manifest for updates only: `update_generation` (int, monotonic), `min_engine_version`,
`key_id`.

---

## 4. OAuth — deliberately minimal

Local apps on a laptop cannot keep a client secret. So:

- **Device-code or loopback-redirect only.** No confidential-client flow, no embedded secret.
- **Short TTL, narrow scope.** A token should name the engine endpoints it may touch (e.g.
  `engine.read`, `routines.write`) — not "everything".
- **Tokens live in the vault**, handed to the app per-call. An app that can read a long-lived token
  off disk is a credential leak waiting for the ship gate's secrets scanner to find it.
- **Consent is per-app and revocable**, and revocation must take effect without a restart.

Cortex's side (G2.6) will treat any token as untrusted input and re-check scope on every call — so
please don't optimise by making tokens self-describing-and-trusted.

---

## 5. Offline is the default, not the fallback

The engine must run fully with **no update server reachable**:

- update check failure = a log line, never a degraded engine;
- no telemetry upload without explicit consent (plan §4.2 already says uplink is opt-in);
- if the vault is locked, the engine keeps running with existing capability and simply cannot
  verify or apply an update until unlocked.

State this as a test in your lane: pull the network, everything still works.

---

## 6. Suggested build order (each step independently useful)

1. **Trust root + pin at install** — `vault.trust_root()`, offline read, generation counter.
2. **`verify_bundle` with anti-rollback** — refuse lower generations; unit tests for
   forged-signature, wrong-key, replayed-old-generation, tampered-content.
3. **`record_update`** — append-only custody audit.
4. **Token issue + scope + revoke** — device-code/loopback; revocation effective immediately.
5. **Rotation** — signed by outgoing key; revoked list only grows.

**Hand back after step 2** if you want Cortex to start G2.6 early — the update port only needs
`trust_root` + `verify_bundle` to be built and tested against a stub for steps 3–5.

---

## 7. What is explicitly NOT in P17a

- Building or hosting bundles (OpenShip).
- Any auto-apply of updates without the human approve step (ship gate stays).
- Cloud key escrow. Keys stay in the user's vault; if that changes it is a product decision, not
  an implementation detail.

---

## Bottom line

Build the **trust root and the verify call, offline and rollback-proof**. Cortex will do the
fetching, applying and scoping on top. The rule that makes this safe is one line: *the key never
travels with the update, and an update never has more power than an app the user imported by hand.*
