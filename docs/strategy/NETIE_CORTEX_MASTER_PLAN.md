# Netie Cortex — Master Plan

**One line:** Governed operational AI (Palantir-AIP class) for Malaysia — layer on incumbents, greenfield for SMEs, consented skill capture, tamper-evident audit.

---

## Horizons

### H1 — Earn (0–12 months)

**Goal:** 1–3 paying warehouse/logistics SMEs on DMS Brain + V0.

| Priority | Ship | Revenue hook |
|----------|------|--------------|
| 1 | F1 audit ledger (if not done) | Governance demo |
| 2 | V0 location + QR + photo intake + scan-move | Beats Excel; FDE setup fee |
| 3 | Chat + task suggest loop (F2–F4) | "AI layer" retainer |
| 4 | One reference case study (video + metrics) | Sales collateral |

**Wedge:** Wedge B (greenfield SMEs on Excel/paper). Not rip-and-replace WMS.

**Kill list (H1):** blockchain L1, RWA tokenization, social network, plush toy, robots, full RUMA Closer integration, V3 vision map, bank-grade PQC crypto.

### H2 — Scale (12–36 months)

- Wedge A: governed layer on mid-market WMS/Snowflake (read-only connectors)
- FDE playbook productized; 3PL B2B2B (TASCO/GDEX-style distribution)
- V1 dimensioning + V2 slotting on paying V0 base
- RUMA Closer (respond.io-class) as second vertical

### H3 — Kingdom (5–20 years)

- Dual-brain: governed execution + world-model planning (goal → safe path → execute)
- Consented task mining → digital workers (augmentation framing, PDPA-compliant)
- Enterprise + bank tier with crypto-agility
- Destination: category leader — not the input to daily decisions

---

## Current phase (June 2026)

**Done:** CortexOS runtime, DMS pack, chat UI, sqlglot guardrails, compliance engine substrate, demo script.

**Now:** V0 warehouse spine (see `docs/dms/VISION_GOVERNANCE.md` § V0).

**Next gate:** Claude Gate 1 — V0 smoke tests + RLS + EXIF strip + ledger proof.

---

## Architecture (what we're building)

```
Customer msg / scan / photo
    → intake (F2) → classify (F3) → suggest task (F4)
    → human approve → compliance gate (F5) → execute
    → audit ledger (F1) → skill capture (F6)
    → encrypted + RLS + PII redaction (F7)
```

Vision (V0–V3) writes to the same spine — no parallel data path.

---

## Marketing (Elon-style premium-first)

1. **Premium narrative:** "Governed warehouse brain — photograph, scan, audit everything."
2. **Scarcity:** Forward-deployed engineer per pilot; limited slots.
3. **Proof before scale:** One warehouse on camera before broad SME push.
4. **Reinvest:** Premium setup fees → engineering → lower-touch tier later.

---

## Investment narrative (one paragraph)

Malaysia logistics SMEs run on Excel; listed players run WMS but lack governed AI over operations. Cortex is a vertical operational system + tamper-evident audit layer — not another warehouse. Category: intelligent process automation (~$20B, growing). Moat: compliance engine + consented skill capture + FDE embedding. TAM expands via 3PL B2B2B. Ask: seed to put 3 warehouses on V0 and publish one case study.

---

## Decision log

| Decision | Rationale |
|----------|-----------|
| Layer, don't rip-and-replace | Switching cost kills enterprise sales |
| V0 before vision magic | Demoable without ML risk |
| Record-now / batch-learn | Auditable; no silent model drift |
| Consented capture only | PDPA 2010 + operational buy-in |
| `CortexOS` canonical, `netie` alias | Backward compat for tests/imports |
