# DMS lane — Active map (2026-07-31)

**Parent (engine-first):** [`docs/ACTIVE.md`](../ACTIVE.md) — read that if the work touches AirGPT, Pointer, OpenVault, or shared engine APIs.  
**This file** is only the **DMS / Spaces / eval** lane.

---

## DMS orientation

| Layer | What it is | What it is not |
|-------|------------|----------------|
| **DMS product** | `D:\DMS` — Spaces / ChatGPT-for-Excel+DB | The Cortex engine |
| **DMS pack** | `packs/dms/` inside Cortex | Pointer Act UI |
| **Spaces sandbox** | ACL ∩ selected sources (data-plane) | WASM / Firecracker |
| **Pointer** | Peer app at `D:\Netie Clicks` | Part of DMS Spaces demo |

```
Postgres Phase0 + RLS → Amend Proposal → Spaces persist
     ↕
C7 honesty + eval N→310 → BIRD  |  C5→C8 (P22)
```

OpenVault → G2.6 is an **engine/adoption** blocker (see parent ACTIVE), not DMS-only.

---

## Canonical docs (DMS day-to-day)

| # | Doc | Role |
|---|-----|------|
| 1 | [`docs/strategy/DMS_SPACES_PRODUCT_2026-07-29.md`](../strategy/DMS_SPACES_PRODUCT_2026-07-29.md) | Product lock |
| 2 | [`docs/dms/packets/NEXT_LANES.md`](packets/NEXT_LANES.md) | Lane prompts |
| 3 | [`docs/dms/packets/CLAUDE_CODE_HANDOFF_NEXT.md`](packets/CLAUDE_CODE_HANDOFF_NEXT.md) | Ports, I1–I3, Trust/Ontology |
| 4 | [`docs/dms/DMS_EVAL_AND_STRESS_PLAN.md`](DMS_EVAL_AND_STRESS_PLAN.md) | Envelope + corpus |
| 5 | Binding packets `C3`/`C4`/`T7`/`C7`/`C10` | Contract law |
| 6 | [`STATUS.md`](../../STATUS.md) | Live gate |

Engine north star / whitepaper: parent [`docs/ACTIVE.md`](../ACTIVE.md).

---

## DMS work queue (W1–W12)

→ [`docs/bin/subagent-results/2026-07-31_active-work-queue.md`](../bin/subagent-results/2026-07-31_active-work-queue.md)

**Subagent memory:** [`docs/subagents_findings/INDEX.md`](../subagents_findings/INDEX.md)

**NOW (DMS floor — before H-depth):** [`DMS_ANCHORED_SEQUENCE.md`](DMS_ANCHORED_SEQUENCE.md)  
C7-prod → claim_n review → Postgres → Amend → Spaces → C11  
**DONE:** W3 C7 Protocol · C5-min · C8-min · T7 live drillthrough · exclusion clarify  
**QUEUED after DMS floor:** [`NETIE_ENGINE_DEPTH_PLAN`](../strategy/NETIE_ENGINE_DEPTH_PLAN_2026-07-31.md) H0–H6 (pull H1/C7/Act earlier only if DMS improves)  
**Parked:** CRAG/BIRD until Spaces · OpenVault merge (dirty) · P1 marketing

**Clarified THROW_AWAY:** see parent ACTIVE — Pointer/JEPA/Palantir/Closer are **not discarded**; they are out-of-lane or parked.

---

## Reference in `docs/dms/`

`DMS_ANCHORED_SEQUENCE.md` · `SUPERVISOR_GATE.md` · `TRUTH_GROUND_MAP.md` · `ROUTER_STATES.md` · `BUILD_PLAN_V2_LAKEHOUSE.md` · `POSITIONING.md` · `VISION_GOVERNANCE.md` · `DMS_TECHNICAL_ARCHITECTURE.md` · historical `BUILD_PLAN.md`

Sandbox (tools + Docker + Spaces ACL): [`SANDBOX_ORIENTATION.md`](SANDBOX_ORIENTATION.md)
