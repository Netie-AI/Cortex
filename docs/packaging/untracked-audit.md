# Untracked / ignored audit under `CortexOS/`

**Date:** 2026-07-29  
**Tree:** `main` @ `aea1ab2` (`feat(contract): … 1.1.0`)  
**Compared to:** local branch `netie-engine` (no `rnd/netie-engine` remote on this clone)  
**Commands:** `git status --porcelain`, `git ls-files --others --exclude-standard`, `git status --porcelain --ignored -- CortexOS/`

Classification: **(a)** real engine code that must be committed · **(b)** generated/cache/runtime that should stay ignored · **(c)** dead / foreign mirror

## Verdict

The agentic engine tree under `CortexOS/` is **already tracked** on `main` (209 paths). There is no untracked agentic backlog to fold into a release commit. The only untracked non-ignored `CortexOS/` file on the live `D:\Cortex` worktree at audit time was Claude's in-flight C3 file (`CortexOS/execution/manifest.py`) — leave that to the C3 lane.

| Path | Tracked? | `.gitignore` | Class | Notes |
|------|----------|--------------|-------|-------|
| `CortexOS/execution/manifest.py` | no (C3 WIP on `D:\Cortex` main WT) | — | **(a)** | Signed-manifest verifier — **C3 owns this**; do not land in `fix(release)`. |
| `tests/test_execution/test_manifest_verification.py` | no (C3 WIP) | — | **(a)** | C3 tests; same rule. |
| `CortexOS/**/__pycache__/` | no | `__pycache__/` | **(b)** | Bytecode. |
| `CortexOS/.env` | no | `.env` | **(b)** | Local secrets. |
| `CortexOS/data/` (entire tree) | no | `CortexOS/data/` | **(b)** | Runtime. Includes `telemetry/keys/*.json` with per-device `netie_kek` — never commit. |
| `CortexOS/AirGPT/` | no | `CortexOS/AirGPT/` | **(c)** | Working mirror of `D:/AirGPT` (own git). Present on disk (~492 files). On `netie-engine` these paths were tracked; on `main` they are correctly ignored. |
| `CortexOS/Vertex/` | no | `CortexOS/Vertex/` | **(c)** | Same pattern (~29 files). Ignored on `main`; tracked only on stale `netie-engine`. |
| All other `CortexOS/**/*.py` modules (api, execution, dms, rag, …) | **yes** | — | **(a)** | Landed in the engine backlog commits already on `origin/main` through `9417fda` (+ local `aea1ab2`). |

## `netie-engine` vs `main`

| Metric | Count |
|--------|-------|
| `CortexOS/` files on `main` | 209 |
| Paths on `netie-engine` not on `main` | 226 |
| …of which `CortexOS/AirGPT/**` | 197 |
| …of which `CortexOS/Vertex/**` | 29 |
| …non-mirror engine code | **0** |

Nothing agentic exists only on `netie-engine`. Do **not** resurrect AirGPT/Vertex into `main`.

## Related chip (outside `CortexOS/`)

| Path | Class | Notes |
|------|-------|-------|
| `packs/data/dms_ops.db` | tracked binary | Rewritten at runtime; dirties the tree. Should become generated (gitignore + seed script) in a later chore — not this release commit. |
| `data/{engine,bench,workflows,apps,lakehouse,ingest_drop}/` | **(b)** | Already ignored; regenerated on boot. |

## Action for R1.1

No separate “commit the agentic tree” commit is required. Proceed with `fix(release):` infra only; leave C3 untracked files alone.
