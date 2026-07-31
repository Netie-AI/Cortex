# Cursor → Claude verify packet — Activity panel / App runner / L0

**Date:** 2026-07-25 → 2026-07-26  
**Author:** Cursor (implementer)  
**Verifier:** Claude (cold session — do not trust Cursor’s “green” claims; re-run commands below)

**STATUS: READY FOR CLAUDE VERIFY**

---

## Do not touch (parallel track)

These tracked files belong to another lane — verify they were **not** modified for this work beyond pre-existing dirty state:

- `CortexOS/execution/dag_runner.py` (pre-existing parallel dirty; this lane did not edit it)
- `CortexOS/agent_sdk/hooks.py`
- `demo/dms-ui/**`
- `CortexOS/api/engine_routes.py` / `memory_routes.py`

Shared files may only carry **additive** registration / append-only STATUS blocks:

- `CortexOS/api/app.py` — route registration only (`+20/0`)
- `CortexOS/api/lakehouse_routes.py` — `+1/0` (`from __future__ import annotations` from netie-engine)
- `STATUS.md` / `PARKING_LOT.md` — dated append blocks only

---

## Slice 1 — AirGPT Engine Activity panel — DONE

### Change map

| Repo | Path | Kind | Intent |
|------|------|------|--------|
| AirGPT | `cortex_client.py` | additive | `engine_activity()` → `GET /api/engine/activity` soft-fail |
| AirGPT | `clipdrop.py` | additive | Proxy `GET /api/engine/activity` |
| AirGPT | `index.html` | additive | `engineActivityCardHtml` in `showHostingPage` + quiet-poll refresh |
| Cortex | `bench/stress.py` | additive | `--scenario activity` |

### Verify commands

```powershell
cd D:\Cortex
$env:DMS_AUTH_DISABLED=1; $env:PACK=dms
python -m pytest tests/dms/test_agent_engine_routes.py::test_activity_control_panel -q
python -m bench.stress --scenario activity --threads 4 --iterations 10
# Live (engine on :8010):
Invoke-WebRequest http://127.0.0.1:8010/api/engine/activity -UseBasicParsing | Select-Object StatusCode
# Live (AirGPT on :8765, after proxy — start clipdrop if needed):
Invoke-WebRequest http://127.0.0.1:8765/api/engine/activity -UseBasicParsing | Select-Object StatusCode
```

### Cursor local results

- [x] Implemented
- [x] Stress `activity` → errors: 0 (20/20 ok @ 4×5)
- [x] Live `:8010/api/engine/activity` → **200** (budget 0/25 MYR, `apps.running` present)
- [ ] `:8765` proxy — AirGPT not running during Cursor gate; Claude should start clipdrop and probe

### Acceptance

- Both proxies return JSON with `ok`, `routines`, `workflows`, `races`, `apps`.
- Hosting page shows Engine activity card when Cortex is live.

---

## Slice 2 — Approved-app process runner — DONE

### Change map

| Repo | Path | Kind | Intent |
|------|------|------|--------|
| Cortex | `CortexOS/execution/app_runner.py` | **new** | subprocess supervisor |
| Cortex | `CortexOS/execution/app_store.py` | additive | run_status/pid columns; start/stop; stop-on-delete; DB-unique ports |
| Cortex | `CortexOS/api/app_routes.py` | additive | `POST .../start`, `POST .../stop` |
| Cortex | `CortexOS/api/activity_routes.py` | additive | `apps.running[]` |
| Cortex | `tests/dms/test_app_runner.py` | **new** | static zip start→HTTP→stop |
| Cortex | `bench/stress.py` | additive | `--scenario apps` + `routines` |

### Verify commands

```powershell
cd D:\Cortex
$env:DMS_AUTH_DISABLED=1; $env:PACK=dms
python -m pytest tests/dms/test_app_runner.py tests/dms/test_apps_importer.py -q
python -m bench.stress --scenario routines --threads 4 --iterations 8
python -m bench.stress --scenario apps --threads 2 --iterations 3
```

### Cursor local results

- [x] Implemented (python+static full; node best-effort; docker → `unsupported_stack`)
- [x] `pytest` app_runner + importer + activity → **13 passed**
- [x] Stress `routines` → errors: 0, wedged_running: false
- [x] Stress `apps` → errors: 0 (4/4 cycles)

### Acceptance

- Static approved app listens on assigned 88xx port; stop frees port.
- No auto-start on approve/import.

---

## Slice 3 — L0 DuckLake (Option B via C reconcile) — DONE

### Diff result (`main` vs `netie-engine`)

```
git diff --stat main netie-engine -- packs/dms/lakehouse/ CortexOS/api/lakehouse_routes.py scripts/lakehouse_migrate.py tests/dms/test_l0_lakehouse.py
→ CortexOS/api/lakehouse_routes.py | 1 +   (only `from __future__ import annotations`)
→ packs/dms/lakehouse/*, migrate, test_l0_lakehouse: identical
```

### Change map

| Repo | Path | Kind | Intent |
|------|------|------|--------|
| Cortex | `CortexOS/api/lakehouse_routes.py` | additive +1 | Port future-annotations from netie-engine |
| Cortex | `STATUS.md` | append | Close L0 next-move; dated block 2026-07-26 |
| Cortex | `PARKING_LOT.md` | append | P15 L0 reconciled note |

### Verify commands

```powershell
cd D:\Cortex
git diff --stat main netie-engine -- packs/dms/lakehouse/ CortexOS/api/lakehouse_routes.py scripts/lakehouse_migrate.py tests/dms/test_l0_lakehouse.py
python -m pytest tests/dms/test_l0_lakehouse.py -q
python -m bench.stress --scenario stream --threads 4 --iterations 10
python -m bench.stress --scenario all --threads 2 --iterations 3
```

### Cursor local results

- [x] Diff recorded (1 line only)
- [x] `test_l0_lakehouse` → **14 passed**
- [x] Stress `stream` → errors: 0
- [x] Stress `all` (ledger/query/stream/discovery/activity/routines/apps) → **all errors: 0**

---

## READY FOR CLAUDE VERIFY — checklist

---

## CLAUDE VERIFY RESULT — 2026-07-26 (re-ran every command; boxes not trusted)

| Slice | Verdict | Evidence |
|---|---|---|
| 1 — Activity panel | **PASS** | `test_activity_control_panel` + suite → 34 passed; stress `activity` errors 0; live `:8010/api/engine/activity` → **200** (sections `ok,ts,routines,workflows,races,apps`, `apps.running` present); **`:8765` proxy → 200** — Cursor's open box now closed |
| 2 — App runner | **PASS with fixes** (see below) | `test_app_runner` + `test_apps_importer` → passed; stress `routines` errors 0 / `wedged_running` false; stress `apps` 6/6 cycles, errors 0 |
| 3 — L0 DuckLake | **PASS** | `git diff main netie-engine` over the L0 paths = **1 insertion**, exactly `from __future__ import annotations` in `lakehouse_routes.py`; `test_l0_lakehouse` green in-suite |

### Two defects found in `app_runner.py` and fixed (Claude lane)

Cursor's tests covered the happy path only. Both defects are now regression-tested
(`tests/dms/test_app_runner.py`, 8 passed):

1. **Bare-pid kill could terminate an unrelated process.** After an engine restart
   `_PROCS` is empty, so `stop()` fell through to `os.kill(stored_pid, 15)` then an
   unconditional `SIGKILL`. A recycled pid means killing an innocent process on the
   user's machine. Now a pid without a live handle is signalled **only while the app's
   port is still listening**; otherwise it returns `stale_pid` and kills nothing.
   `stop()` takes `port=`; `app_store.stop_app` passes it and reports `terminated` /
   `reason` instead of always claiming success.
2. **False-positive health check.** `_wait_port` treated "something is listening" as
   success, so a crashed app whose port was held by any other process reported
   `ok: True` with a dead pid recorded as running — and `start()` only checked port
   occupancy when a pid was already on file. Now `start()` refuses an occupied port
   (`port_conflict:<port>`), and `_wait_port` requires the spawned process to still be
   alive, returning `process_exited` fast instead of burning the full 15 s timeout.

Also added `stop_all()` + `atexit` so an engine restart stops supervised children
rather than orphaning them (orphans are what create the stale pids in defect 1).

### Packet footgun — `$env:DMS_AUTH_DISABLED=1`

The verify preamble above sets `DMS_AUTH_DISABLED=1`. That is correct for the targeted
route tests, but running the **full** suite in the same shell fails 3 RBAC tests
(`test_l1_ingest::test_upload_api_rbac_and_ledger`, `test_s0_streams::test_events_api_rbac_and_backpressure`,
`test_s1_agents::test_agent_api_rbac`) — they assert 401/403 and auth is disabled out from
under them. All 3 pass in a clean shell. Run the full suite **without** that variable:
`537 passed, 9 skipped`, secrets scan clean.

### Live-environment note

At verify time `:8010` had a listener (pid 52220) whose HTTP never answered — a wedged
uvicorn from an earlier launch, not a code fault. Killed and relaunched; engine healthy,
both activity endpoints 200. Worth knowing the launcher can leave a wedged listener behind.

---

- [x] Change maps complete
- [x] Stress JSON: `bench/results/stress_last_run.json`
- [x] Git-diff audit: shared files additive-only (`app.py` +20/0, `lakehouse_routes` +1/0); `dag_runner` dirty but pre-existing parallel — do not attribute to this lane
- [x] No commits (owner call)
- [ ] Claude re-runs verify commands above and marks each slice PASS/FAIL

### Paste-to-Claude opener

```
Read docs/dms/packets/CURSOR_TO_CLAUDE_ACTIVITY_RUNNER_L0_2026-07-25.md.
Re-run every Verify command. Do not trust Cursor’s checked boxes.
Confirm: (1) activity proxy + panel contracts, (2) app start/stop on 88xx,
(3) L0 lakehouse identical except future-annotations, (4) stress errors==0.
Report PASS/FAIL per slice with command output evidence.
```
