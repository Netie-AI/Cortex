# Cortex consumers — sibling apps & packs

**Date:** 2026-07-31 · **Parent map:** [`docs/ACTIVE.md`](../ACTIVE.md) · **Roles:** [`PRODUCT_ROLES.md`](../../PRODUCT_ROLES.md)  
**Inventory evidence:** [`docs/bin/subagent-results/2026-07-31_sibling-repos-inventory.md`](../bin/subagent-results/2026-07-31_sibling-repos-inventory.md)

Cortex is one engine. Apps plug in; they do not fork the brain.

---

## Live mesh (this machine)

| Surface | Path | Talks to Cortex how | Demo vs product |
|---------|------|---------------------|-----------------|
| **Engine** | `D:\Cortex` | SoT — `:8000` pack routes, `:8010` engine | — |
| **DMS** | `D:\DMS` | HTTP + `cortex-contract`; **never** `import CortexOS` | Product UI; smoke still in `demo/dms-ui` |
| **OpenVault** | `D:\OpenVault` | Keys / gate / FreeRoute; Cortex asks, OV allows | Companion product (`:5000`) |
| **AirGPT** | `D:\AirGPT` | Thin HTTP client → `:8010`; Seek/Routines/Apps proxies | Host shell (`:8765`) |
| **Pointer** | `D:\Netie Clicks` | Act fail-closed → `:8010` `computer_control` | External Act client |
| **Netie Space** | `D:\Space` | Optional handoff / preview UI | Not DMS Spaces ACL product |
| **RUMA** | `D:\RUMA` + `packs/ruma` | Planned; **not wired** | Parked vertical |
| **OpenIDE** | (out of tree) | Asks Cortex for coding tools | Expert slice |

**Missing placeholders (do not create blindly):** `D:\Pointer`, `D:\Clicks`, `D:\FreeRoute` — names already mapped above.

---

## In-repo packs (register into engine)

| Pack | Role |
|------|------|
| `packs/dms/` | First reference consumer — semantic, ontology, lakehouse, skills |
| `packs/crm/` | Scaffold |
| `packs/ruma/` | Parked Closer's vertical |

Rule: `CortexOS/**` never imports `packs.*`. Packs register via engine Protocols.

---

## What “out of DMS demo” means

**DMS Spaces demo** focuses on Excel/DB sandboxes, amend, Trust/eval honesty.  
It must **not** pull Pointer Act chrome into that story.

That does **not** mean Pointer is unimportant: it is a **peer consumer** of the same engine (Act + OSR bands). Develop Pointer in `D:\Netie Clicks`; improve Act/presets in `CortexOS/execution/`.

Same for:
- **Closer / respond.io** → RUMA lane when un-parked (P4/P9)
- **Palantir full parity** → ontology depth in engine + FDE per consumer (P1)
- **MemPalace / trained JEPA** → engine memory durability roadmap — claim only when measured shipped

---

## Ports (typical)

| Port | Owner |
|------|-------|
| 8000 / 8010 | Cortex |
| 5000 | OpenVault |
| 8765 | AirGPT |
| 8090+ | DMS gateway (sibling) |
| 3000 | Demo / product web (context-dependent) |
