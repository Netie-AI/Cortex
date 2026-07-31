# Sibling / related repo inventory (Windows `D:\`)

**Date:** 2026-07-31  
**Machine:** Windows 10 (`win32 10.0.26200`)  
**Method:** `Get-ChildItem D:\`, per-repo `README` / `AGENTS.md` / `STATUS`, light content grep for `cortex-contract` / `CortexOS` / HTTP URLs, `.git/config` for remotes where `git` reported dubious ownership.

---

## Executive summary

| Role | Path(s) |
|------|---------|
| **Engine (SoT)** | `D:\Cortex` |
| **Engine consumer** | `D:\DMS` |
| **Companion vault / FreeRoute** | `D:\OpenVault` |
| **UI shell** | `D:\AirGPT`, `D:\Netie Space` |
| **Act / Pointer client** | `D:\Netie Clicks` (docs alias: Pointer; **not** `D:\Pointer`) |
| **Parked vertical** | `D:\RUMA`, `D:\Cortex\packs\ruma`, `D:\Cortex\activeflow\` |
| **Marketing / sibling-only** | `D:\Landing` |
| **Secrets (non-repo)** | `D:\NetieSecrets` |
| **Missing / empty placeholders** | `D:\Pointer`, `D:\Clicks`, `D:\FreeRoute`, `D:\OmniRoute` (empty dir) |
| **Netie-AI org, not Cortex runtime** | `D:\Cassandra`, `D:\OpenForge` |
| **Placeholder** | `D:\Netie Cortex` |

Canonical product-role contract (shared across repos): `D:\Cortex\PRODUCT_ROLES.md`.

---

## Checked paths — existence

| Path | Exists |
|------|--------|
| `D:\Cortex` | Yes |
| `D:\DMS` | Yes |
| `D:\OpenVault` | Yes |
| `D:\AirGPT` | Yes |
| `D:\Pointer` | **No** — use `D:\Netie Clicks` |
| `D:\Clicks` | **No** — use `D:\Netie Clicks` |
| `D:\FreeRoute` | **No** — FreeRoute is a **product surface inside OpenVault**, not a separate repo |
| `D:\RUMA` | Yes |
| `D:\Netie Cortex` | Yes |
| `D:\Netie Space` | Yes |
| `D:\Netie Clicks` | Yes |
| `D:\NetieSecrets` | Yes |
| `D:\OmniRoute` | Yes (directory exists, **0 files**) |
| `D:\Landing` | Yes |
| `D:\Cassandra` | Yes |
| `D:\OpenForge` | Yes |
| `D:\Cassandra-backup` | Yes |

---

## Per-repo inventory

### `D:\Cortex` — engine (not a consumer)

| Field | Value |
|-------|-------|
| **Git** | Yes — `origin` → `https://github.com/Netie-AI/Cortex` |
| **Purpose** | Governed agentic runtime / **Netie Engine** — answer plane, execution, ledger, compliance, semantic layer, DMS reference pack (`README.md`, `STATUS.md`). |
| **Cortex dependency** | **Is** Cortex. Ships `packages/cortex_contract/`, `contract/openapi-*.json`, `CortexOS/`, `packs/dms/`. |
| **Docs classification** | **Engine** (source of truth). Not a consumer. |

---

### `D:\DMS` — reference consumer → product home

| Field | Value |
|-------|-------|
| **Git** | Yes — `origin` → `https://github.com/Netie-AI/dms.git` |
| **Purpose** | Forward-deployable **ChatGPT for Excel & databases**; DMS Spaces product (`README.md`, `AGENTS.md`, `STATUS.md`). |
| **Cortex dependency** | **HTTP client** via `packages/cortex_client`; **pins `cortex-contract>=1.2.0,<2`** (`packages/executor/pyproject.toml`, `CLAUDE.md`); vendored OpenAPI `openapi-1.2.0.json`; compose pins Cortex engine image. **Never imports `CortexOS`.** Builds contract wheel from sparse Cortex checkout in CI. |
| **Docs classification** | **Engine consumer** (`docs/dms/ACTIVE.md`, `PRODUCT_ROLES.md`, `CORTEX_WHITEPAPER.md`). |

---

### `D:\OpenVault` — keys, gate, FreeRoute, ship

| Field | Value |
|-------|-------|
| **Git** | Yes — `origin` → `https://github.com/Netie-AI/OpenVault.git` |
| **Purpose** | Local control plane: NVMe/GPU observe, model slots, encrypted key vault, deploy/ship, mesh peers (`README.md`, `STATUS.md`). UI `:3010`, API `:5000`. |
| **Cortex dependency** | **HTTP mesh client** — `OpenMW/openmw/openvault/mesh/cortex_client.py`, routes `/api/cortex/*`, `deploy/from-cortex`. Peer URL wiring to `http://127.0.0.1:8000` (or `:8010` in stack scripts). **No `CortexOS` import**; sibling only for paths. **FreeRoute** (OmniRoute-class routing + budget) lives here, not in `D:\FreeRoute`. |
| **Docs classification** | **Companion vault** (`PARKING_LOT.md` P17a, `CORTEX_WHITEPAPER.md`). |

---

### `D:\AirGPT` — host shell / control plane

| Field | Value |
|-------|-------|
| **Git** | Yes — `origin` → `https://github.com/jian-hong/AirGPT.git` |
| **Purpose** | Local-first AI clipboard, chat, phone-to-PC sync; thin control plane on `:8765` (`README.md`, `PRODUCT_ROLES.md`). |
| **Cortex dependency** | **HTTP** via local `cortex_client.py` (urllib to `CORTEX_API_URL`, default `:8010`; can autospawn `D:\Cortex\scripts\start_cortex_engine.ps1`). **OpenVault** via `openvault_bridge.py` / `/api/openvault/`. No `cortex-contract` pip pin found; no `CortexOS` import. |
| **Docs classification** | **UI shell** (`CORTEX_WHITEPAPER.md`, `PRODUCT_ROLES.md`). |

---

### `D:\Netie Clicks` — Pointer / Act client

| Field | Value |
|-------|-------|
| **Git** | Yes — `origin` → `https://github.com/jian-hong/NetieClicks.git` |
| **Purpose** | Windows screen buddy — region capture, ask/act, fail-closed Cortex gate (`README.md`, `ECOSYSTEM.md`). Package name: `netie-pointer`. |
| **Cortex dependency** | **HTTP** to Cortex `:8010` — `POST /dms/secure`, health (`electron/netie/ecosystem.js`). **OpenVault** `:5000` for LLM/vision keys. No `CortexOS` / `cortex-contract`. |
| **Docs classification** | **Act / Pointer client** (`docs/dms/ACTIVE.md`, `skill_distill/captures/2026-07-29_pointer-demo_dms-lake-map.md`). |

---

### `D:\Netie Space` — file preview shell

| Field | Value |
|-------|-------|
| **Git** | Yes — `origin` → `https://github.com/Netie-AI/Space.git` |
| **Purpose** | Press Space to preview files (PDF/Office/media/code), convert, optional AI chat (`README.md`). |
| **Cortex dependency** | **Optional / design-time** — `docs/CORTEX_RUNTIME_BEST_FINAL.md` describes Space↔AirGPT↔Cortex handoff; UI shows “Netie Cortex · thinking…” but **no `cortex-contract` or `CortexOS` import** in tree. Sibling contract sharing only. |
| **Docs classification** | **UI shell** (front peer in `Netie Clicks/ECOSYSTEM.md`). *Not* the same as **DMS Spaces** (product in `D:\DMS`). |

---

### `D:\RUMA` — parked property vertical

| Field | Value |
|-------|-------|
| **Git** | **No** (no `.git` directory) |
| **Purpose** | Deploy-ready Next.js 14 + Supabase property listings app (`README.md`). |
| **Cortex dependency** | `.env.example` / `.env.local` mention **Cortex v2** URLs but **no in-repo HTTP client or `cortex-contract` pin** found. Planned consumer per `D:\Cortex\docs\bin\verticals\RUMA_PHASE3_5.md` and `packs/ruma/`. |
| **Docs classification** | **Parked vertical** (`docs/README.md`, `docs/dms/SANDBOX_ORIENTATION.md`, `CORTEX_FINAL_GOAL.md`). |

---

### `D:\NetieSecrets` — secrets outside git trees

| Field | Value |
|-------|-------|
| **Git** | No |
| **Purpose** | Credentials that must not live in repos — `Cortex.env.local` for demo/LLM keys (`README.md`). |
| **Cortex dependency** | Loaded by `D:\Cortex\demo\run_demo.ps1` and referenced in `D:\DMS\STATUS.md`. **Sibling only.** |
| **Docs classification** | **Companion** (operational; not a product surface). |

---

### `D:\Netie Cortex` — placeholder

| Field | Value |
|-------|-------|
| **Git** | No |
| **Purpose** | `README.md` contains only “Coming soon”. |
| **Cortex dependency** | None observed. |
| **Docs classification** | **Unclassified placeholder** (name collision with engine branding). |

---

### `D:\Landing` — marketing site

| Field | Value |
|-------|-------|
| **Git** | Yes — `origin` → `https://github.com/Netie-AI/landing.git` (repo root: `netie-agent/`) |
| **Purpose** | Netie AI landing / portfolio (Next.js static export) — `/`, `/suite`, `/cortex/`, product pages (`netie-agent/README.md`). |
| **Cortex dependency** | **Sibling only** — `NEXT_PUBLIC_CORTEX_REPO_URL`, blog copy; no engine import. |
| **Docs classification** | **Marketing / sibling-only** (not in `PRODUCT_ROLES.md` runtime table). |

---

### `D:\Cassandra` — market research (Netie-AI org)

| Field | Value |
|-------|-------|
| **Git** | Yes — `origin` → `https://github.com/Netie-AI/Cassandra.git` (git CLI blocked dubious ownership; read from `.git/config`) |
| **Purpose** | Multi-agent Crash Risk Score (CRS) research system — **not** a trade bot (`README.md`, `STATUS.md`). |
| **Cortex dependency** | **None** found (no `cortex` / `CortexOS` grep hits). |
| **Docs classification** | **Unrelated sibling** (same org, different product). |

---

### `D:\Cassandra-backup`

| Field | Value |
|-------|-------|
| **Git** | Yes (dubious-ownership on CLI; has `.git`) |
| **Purpose** | Backup copy of Cassandra tree (`README.md`, `STATUS.md`). |
| **Cortex dependency** | None. |
| **Docs classification** | **Backup / unrelated**. |

---

### `D:\OpenForge` — analog IC forge

| Field | Value |
|-------|-------|
| **Git** | Yes — `origin` → `https://github.com/Netie-AI/OpenForge.git` |
| **Purpose** | PDF → schematic → SPICE → ngspice training data for analog IC design (`README.md` titles “OpenAnalog”). |
| **Cortex dependency** | **None** found. |
| **Docs classification** | **Unrelated sibling** (Netie-AI org). |

---

### `D:\OmniRoute` — empty placeholder

| Field | Value |
|-------|-------|
| **Git** | No |
| **Purpose** | Empty directory (0 files). |
| **Cortex dependency** | **Concept only** — whitepaper maps **FreeRoute** inside OpenVault to “OmniRoute-class” routing (`CORTEX_WHITEPAPER.md` §4). |
| **Docs classification** | **Absorbed into OpenVault** (not a separate repo on disk). |

---

## Missing paths (documented aliases)

| Requested path | Actual / note |
|----------------|---------------|
| `D:\Pointer` | **Missing.** Docs map **Pointer** → `D:\Netie Clicks` (`D:\DMS\AGENTS.md`, `PRODUCT_ROLES.md`). |
| `D:\Clicks` | **Missing.** Use `D:\Netie Clicks`. |
| `D:\FreeRoute` | **Missing.** Product surface is **FreeRoute** inside `D:\OpenVault`. |

---

## App-like folders **inside** `D:\Cortex`

| Path | What it is | Cortex relationship |
|------|------------|---------------------|
| `demo/` | Engine demo launcher | `run_demo.ps1` loads `D:\NetieSecrets\Cortex.env.local`; `DMS_PRODUCT_HOME.md` points product work to `D:\DMS`. |
| `demo/dms-ui/` | Legacy **engine smoke UI** (Next.js) | Talks to Cortex engine with `PACK=dms`; **not** the product UI. |
| `activeflow/activepieces/` | Gitignored Activepieces clone (~27k files) | **Unwired.** Parked with RUMA (`docs/dms/ACTIVE.md`, `RUMA_PHASE3_5.md`). Safe to delete. |
| `packs/dms/` | DMS reference **pack** (ontology, semantic, ops DB) | Registers into engine; not a separate app repo. |
| `packs/ruma/` | RUMA **pack stub** (`"""RUMA Brain — property vertical pack."""`) | Parked vertical in-engine. |
| `packs/crm/` | CRM pack scaffold | Additional vertical pack. |
| `netie/` | Minimal Python package (`__init__.py` only) | Stub namespace. |
| `CortexOS/AirGPT/` | Referenced in whitepaper repo map | **Not present / empty** on this machine (0 files). AirGPT SoT is `D:\AirGPT`. |
| `wasm_modules/` | `base_agent.wasm` / `.wat` | WASM scaffold (P2 parked; not F8 execution path). |
| `eval/` | `harness.py`, `judges/` | Engine eval harness. |
| `skills/` | YAML skill definitions | Engine skill mesh. |
| `bench/` | Golden corpora, `live_probe.py` | References `DMS_ROOT` default `D:\DMS`. |

---

## Cross-references in `D:\Cortex` (sibling paths)

Light grep for `D:\DMS`, `D:\OpenVault`, `D:\AirGPT`, `D:\Netie*`:

| Path referenced | Example locations |
|-----------------|-------------------|
| `D:\DMS` | `demo/DMS_PRODUCT_HOME.md`, `docs/dms/ACTIVE.md`, `bench/corpus.py`, `bench/live_probe.py`, `docs/eval/BENCHMARK_INVENTORY.md`, handoff packets |
| `D:\OpenVault` | `PARKING_LOT.md` P17a, `docs/dms/packets/CLAUDE_CODE_HANDOFF_NEXT.md`, research findings |
| `D:\AirGPT` | `STATUS.md`, `docs/research/findings/NEXT_SLICES_ROUTINES_CFSM_ADDAPP_2026-07-25.md`, whitepaper |
| `D:\Netie Clicks` | `docs/dms/ACTIVE.md`, `skill_distill/captures/2026-07-29_pointer-demo_dms-lake-map.md`, `D:\DMS\AGENTS.md` (as Pointer) |
| `D:\NetieSecrets` | `demo/run_demo.ps1`, `D:\DMS\STATUS.md` |

No `D:\Cortex` doc hits for `D:\Landing`, `D:\Cassandra`, or `D:\OmniRoute` on disk.

---

## Other `D:\*` directories (full `D:\` listing)

Top-level folders on this machine that are **not** Cortex-ecosystem runtime peers:

`DCIM`, `FYP`, `FYP_MASTER`, `FSA1`, `Analog KG`, `Digital_KG`, `RESUME`, `OF_data`, `BaiduNetdiskDownload`, `Bak`, `PPT`, `dist`, `proc`, `Red Hat Enterprise Linux 7 64-bit_TSMC_inc_GP`, etc.

Treat as personal / academic / unrelated unless a future doc explicitly wires them to Cortex.

---

## Dependency model (quick reference)

```
                    ┌─────────────────────────────────────┐
                    │  UI shells: AirGPT, Netie Space      │
                    │  Act client: Netie Clicks (Pointer)   │
                    └──────────────┬──────────────────────┘
                                   │ HTTP
                    ┌──────────────▼──────────────────────┐
                    │  D:\DMS (consumer)                   │
                    │  cortex-contract pin + HTTP client   │
                    └──────────────┬──────────────────────┘
                                   │ HTTP
┌──────────────────────────────────▼──────────────────────────────────┐
│  D:\Cortex — engine (CortexOS, packs, contract/)                     │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ gate / keys / FreeRoute
                    ┌──────────────▼──────────────────────┐
                    │  D:\OpenVault (:5000 / :3010)        │
                    └─────────────────────────────────────┘

Secrets: D:\NetieSecrets (outside all git trees)
Marketing: D:\Landing → links to GitHub repos only
Parked: D:\RUMA, packs/ruma, activeflow/
```

---

## Sources

- Shell: `Get-ChildItem D:\`, per-repo existence checks, `cmd /dir`
- Repo docs: `README.md`, `AGENTS.md`, `STATUS.md`, `PRODUCT_ROLES.md`, `ECOSYSTEM.md`
- Cortex canonical map: `docs/dms/ACTIVE.md`, `docs/strategy/CORTEX_WHITEPAPER.md`, `PARKING_LOT.md`
- Prior subagent: `docs/bin/subagent-results/2026-07-31_legacy-wasm-docker.md`
