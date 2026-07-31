# Subagent result — Legacy repos, prompts, WASM vs Docker

**Date:** 2026-07-31  
**Agent:** explore / legacy inventory  
**ID:** [legacy-inventory](7248fbe6-b25d-4d88-9aaa-a5f2c5633ac0)

---

## Verdict

| Topic | Reality |
|-------|---------|
| **Activepieces / `activeflow/`** | ~27k-file gitignored clone; **zero engine wiring**. Parked with RUMA (P4/P9). |
| **`ruma_flows/` / `flow_call`** | Docs-only (now binned with `RUMA_PHASE3_5.md`). |
| **`DMS Prompts/`** | Emptied: architecture → `docs/dms/`; pricing/plan → `docs/bin/prompts-misc/`. |
| **FABLE5 prompts** | O1–O7 largely shipped → `docs/bin/handoffs/`. |
| **WASM** | Scaffold + tests only; **not** on TOOL_CALL path. |
| **TOOL_CALL / F8** | `dag_runner` → `tool_runner` **host-shim** (allowlist, sanitize, outputs dir, ledger). |
| **Docker** | App `dockerize()`, Dockerfiles for API images, compose for Postgres/Qdrant — **not** code-execution sandbox. |
| **OS sandbox (Seatbelt/gVisor)** | Distill debt only — not implemented. |

## Honesty matrix (use in STATUS / ARCHITECTURE)

| Claim | Accurate? |
|-------|-----------|
| F8 governed tool-call | Yes (host path) |
| Production WASM / Firecracker | No (P2) |
| RUMA Flows integrated | No |
| Docker sandbox for tool code | No (dockerize = apps) |

## Recommended disk cleanup (owner)

```powershell
# Optional — reclaim space; not in git
Remove-Item -Recurse -Force D:\Cortex\activeflow
```
