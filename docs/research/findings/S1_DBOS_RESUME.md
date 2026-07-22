# A1 — DBOS vs Temporal for S1 durable agent resume

**Date:** 2026-07-22  
**Status:** RESEARCH ONLY (no Python/JSX/test code changed)  
**Scope:** FEATURE S1 watcher-agent durable resume (`detect → draft → pending_approval → approve → publish`)

---

## Verdict

**Recommend DBOS Transact (Python library `dbos`) as the primary durable-execution substrate for S1.**

| Criterion | DBOS Transact | Temporal |
|-----------|---------------|----------|
| Architecture | In-process library; checkpoints to SQLite/Postgres | External orchestration cluster + workers + its own datastore |
| Default local store | **SQLite** (zero config) → Postgres via DSN | Needs Temporal server (+ typically Cassandra/visibility) |
| Windows host fit | `pip install`; no Java/Docker/Temporal server | Painful on Windows (Docker/WSL or remote cluster) |
| Adoption cost for Cortex | Annotate existing `employee.py` steps; keep FastAPI routes | Rearchitect to Temporal workers + client API |
| Matches BUILD_PLAN anti-scope | **Yes** — primary path | Explicitly **not** primary |
| Scale-out story | Scale with Postgres / DBOS queues | Documented later path when run volume warrants (P3) |

**Why this wins for Cortex DMS:** S1 already parks runs in ops SQLite (`DMS_OPS_DB`), wants crash-mid-workflow resume without a new ops cluster, and the BUILD_PLAN / CURSOR_EXEC_PACKET already chose DBOS. Temporal adds infrastructure Cortex does not need at current agent volume and contradicts the stated anti-scope.

**Temporal role:** Keep as a **documented scale-out path only** (PARKING_LOT P3 / GATE_F8 note) — e.g. when DAG/agent runs exceed ~100/day and multi-worker fan-out outgrows a library+Postgres model. Do **not** implement Temporal for S1.

---

## Install pins

Verified against PyPI on 2026-07-22.

| Package | Pin | Notes |
|---------|-----|--------|
| **`dbos`** | **`>=2.28.0,<3`** (pin exact **`2.28.0`** for first land) | MIT; Python **`>=3.10`** (matches repo `requires-python`) |
| Transitive (pulled by `dbos`) | `sqlalchemy[asyncio]>=2.0.43`, `psycopg[binary]>=3.1`, `pyyaml>=6.0.2`, `python-dateutil>=2.9.0.post0`, `websockets>=14.0`, `typer-slim>=0.17.4` | `psycopg[binary]` provides Windows wheels |
| Optional extras | `dbos[aiosqlite]` if async SQLite steps are needed; `dbos[otel]` / `dbos[validation]` only if used | Default sync + SQLite needs no extra |

**Repo wiring (B1, not this research):**

```toml
# pyproject.toml — proposed
[project.optional-dependencies]
agents = ["dbos>=2.28.0,<3"]

# poetry mirror
[tool.poetry.dependencies]
dbos = {version = "^2.28.0", optional = true}

[tool.poetry.extras]
agents = ["dbos"]
```

Install:

```bash
pip install "dbos==2.28.0"
# or
pip install -e ".[agents]"
```

**Config pattern (official docs):**

```python
from dbos import DBOS, DBOSConfig
import os

config: DBOSConfig = {
    "name": "cortex-dms-agents",  # 3–30 chars, [a-z0-9_-]
    # Omit / None → SQLite default file (dev)
    # Postgres: postgresql://user:pass@host:5432/dbos_system
    "system_database_url": os.environ.get("DBOS_SYSTEM_DATABASE_URL"),
}
DBOS(config=config)
DBOS.launch()  # recovers incomplete workflows on startup
```

**SQLite → Postgres path:**

| Env | URL shape | Use |
|-----|-----------|-----|
| Dev / pytest | unset → auto `sqlite:///{name}.sqlite`, or `sqlite:///./data/dbos_agents.sqlite` | Default on Windows |
| Prod / multi-process | `DBOS_SYSTEM_DATABASE_URL=postgresql://…` | Required for concurrent workers |
| Ops app data | Keep existing `DMS_OPS_DB` for `dms_agents` / `dms_agent_runs` / F1 ledger | **Separate** from DBOS system DB unless deliberately unified later |

Do **not** conflate DBOS system DB with lakehouse DuckDB. Application registry can stay on `DMS_OPS_DB`; DBOS only stores workflow/step checkpoints.

Docs: [Add DBOS To Your App](https://docs.dbos.dev/python/integrating-dbos) · [Database connections](https://docs.dbos.dev/python/tutorials/database-connection) · [DBOS vs Temporal](https://docs.dbos.dev/explanations/comparing-temporal)

---

## Crash / resume semantics mapped to S1 steps

DBOS guarantees (from workflow tutorial):

1. Interrupted process + restart → resume from **last completed step** (not mid-step).
2. Completed steps are **never re-executed**; a step that crashes mid-body may retry (**at-least-once** inside the step).
3. Workflow functions must be **deterministic**; all I/O / SQL / LLM / filesystem / UUID / time go in `@DBOS.step()`.
4. Human wait: `DBOS.recv(topic)` (durable) + `DBOS.send(workflow_id, msg, topic)` from the approve API — same pattern as DBOS checkout/payment demos.
5. Idempotency: `SetWorkflowID(...)` so a re-POST `/run` with the same key does not double-fire.

### Suggested step breakdown for `employee.py`

| S1 stage | Current code | DBOS mapping | Crash behavior |
|----------|--------------|--------------|----------------|
| **detect** | `detectors.evaluate(...)` + `agent.checked` ledger | `@DBOS.step() def step_detect(agent_id)` | If killed mid-SQL: step retries; if completed: never re-runs detector |
| **draft** | `_draft_report` + `_compliance_verdict` + `record_run(..., pending_approval)` + `agent.detected` / `agent.drafted` | `@DBOS.step() def step_draft(...)` → returns `{run_id, report, verdict}` | Kill mid-draft → retry draft once; after checkpoint, draft not duplicated. Persist `run_id` in step output so resume is stable |
| **pending_approval** | Status in `dms_agent_runs`; API returns | Workflow calls `DBOS.recv("agent_approval", timeout_seconds=…)` (or long durable wait) after publishing status via `DBOS.set_event` | Process kill while waiting: on `DBOS.launch()` recovery, recv continues; **no second draft** |
| **approve** | `approve_run` API (separate call) | Handler: `DBOS.send(workflow_id, {"decision": "approve", "approver": …}, topic="agent_approval")` | Approve is external signal; at-most-once via SetWorkflowID on send if needed |
| **publish** | Write `outputs/<approver>/<run_id>/report.md` + `agent.published` | `@DBOS.step() def step_publish(...)` after recv returns approve | Kill mid-write: step may retry — make publish **idempotent** (same path, overwrite OK; ledger event keyed / check `artifact_path` already set) |
| **reject** | `reject_run` | `DBOS.send(..., {"decision": "reject", ...})` → workflow records rejected, **skips** publish | Same durable wait path |

### Important design choice (smallest correct model)

Today `run_agent` ends at `pending_approval` and `approve_run` is a **separate** function. For true mid-pipeline resume including the human gate, prefer:

```text
@DBOS.workflow()
def agent_run_workflow(agent_id, actor):
    detection = step_detect(agent_id)
    if not detection.fired:
        return step_record_no_trigger(...)
    draft = step_draft(...)          # parks registry at pending_approval
    DBOS.set_event("run_id", draft["run_id"])
    decision = DBOS.recv("agent_approval", timeout_seconds=...)  # durable park
    if decision["decision"] == "approve":
        return step_publish(draft["run_id"], decision["approver"])
    return step_reject(...)
```

`POST /dms/agents/{id}/run` → `DBOS.start_workflow` (return `run_id` / `workflow_id` via `get_event`).  
`POST /dms/agents/runs/{run_id}/approve` → resolve `workflow_id` from registry → `DBOS.send`.

**Chaos-lite acceptance (BUILD_PLAN):** `taskkill /F` mid-`step_draft` or mid-`step_publish` → restart process → `DBOS.launch()` recovers → **one** report artifact, ledger chain valid, no partial lake commit (detectors are read-only; publish only writes under `outputs/`).

---

## How to unskip `test_workflow_resume_after_kill`

Current placeholder (`tests/dms/test_s1_agents.py`):

```python
@pytest.mark.skip(reason="S1 slice: DBOS durable resume not landed yet ...")
def test_workflow_resume_after_kill():
    assert False, "placeholder for DBOS resume chaos-lite"
```

**Unskip checklist (B1 implementation):**

1. Add `[agents]` extra + import `dbos` behind that extra (skip test if `dbos` missing, or require it in DMS CI).
2. In fixture: set `DBOS_SYSTEM_DATABASE_URL` to a **tmp SQLite** file (not `:memory:` if recovery across process restart is asserted); set `DMS_OPS_DB` as today; monkeypatch `employee.OUTPUTS`.
3. Implement durable `agent_run_workflow` + idempotent `step_publish`.
4. Replace skip with a real test that either:
   - **In-process chaos:** run workflow under a hook that raises after `step_detect` completes but before `step_draft` checkpoints, then call recovery / re-enter workflow with same `SetWorkflowID`, **or**
   - **Process chaos (closer to BUILD_PLAN):** subprocess starts workflow, parent `taskkill /F` / `TerminateProcess` after a sync file signals “past detect”, child restart with same system DB URL + `DBOS.launch()`, assert workflow completes once.
5. Asserts:
   - exactly **one** `outputs/.../report.md` (or one pending→approved transition);
   - `registry` shows a single `run_id` for that workflow id;
   - `ledger.verify().ok`;
   - detector / draft ledger events not duplicated beyond at-least-once step policy (prefer: draft step completed once).
6. Remove `@pytest.mark.skip` and the `assert False` placeholder.
7. Keep `test_agent_chat_dispatch` skipped until B2 (`@agent` chat) — out of scope for DBOS resume.

---

## Windows notes

| Topic | Finding |
|-------|---------|
| Host OS | Repo targets Windows (`taskkill /F` in BUILD_PLAN acceptance). DBOS is a pure Python library — **no Temporal server, no Docker required** for the default path. |
| Install | `pip install dbos==2.28.0` works on Win10/11 with Python ≥3.10; `psycopg[binary]` ships Windows wheels. |
| SQLite paths | Prefer relative `sqlite:///./data/dbos_agents.sqlite` or forward-slash absolute `sqlite:///C:/…/dbos.sqlite`. Avoid ambiguous `sqlite://C:\…` backslash URLs. |
| Postgres | Optional via `DBOS_SYSTEM_DATABASE_URL`; can reuse existing `postgres` extra mindset (`sqlalchemy`/`asyncpg` already optional) — DBOS itself wants `psycopg`. |
| Chaos test | Use `taskkill /F /PID …` or `subprocess` + `creationflags` carefully; file-based barriers under `tmp_path` are more reliable than sleep races. |
| Admin server | DBOS may start an admin HTTP port (default 8080). In tests set `run_admin_server: False` in config to avoid port fights on Windows CI/dev. |
| Temporal on Windows | Possible via Docker Desktop / remote cloud, but heavy and out of anti-scope — do not gate S1 on it. |

---

## Links to truth-ground files in this repo

| Path | Why it matters |
|------|----------------|
| `D:\Cortex\docs\dms\BUILD_PLAN_V2_LAKEHOUSE.md` (§ FEATURE S1 ~L357–390) | Canonical build: DBOS dep, step list, anti-scope (no Temporal primary), smoke tests, chaos-lite |
| `D:\Cortex\docs\dms\CURSOR_EXEC_PACKET_2026-07-22.md` | A1 research task + B1 implementation slice |
| `D:\Cortex\packs\dms\agents\__init__.py` | Explicit: durable-resume (DBOS) is a later slice |
| `D:\Cortex\packs\dms\agents\employee.py` | Governed sync workflow today (detect→draft→approve→publish) |
| `D:\Cortex\packs\dms\agents\registry.py` | Ops SQLite `dms_agents` / `dms_agent_runs` via `DMS_OPS_DB` |
| `D:\Cortex\packs\dms\agents\detectors.py` | Pure SQL detectors (no LLM) — must stay steps, not workflow body |
| `D:\Cortex\CortexOS\api\agent_routes.py` | FastAPI hire/run/approve/reject — send/recv integration point |
| `D:\Cortex\tests\dms\test_s1_agents.py` | `test_workflow_resume_after_kill` skipped placeholder |
| `D:\Cortex\PARKING_LOT.md` (P3) | Temporal/durable DAG deferred; S1 activates **DBOS** path; resume still open |
| `D:\Cortex\STATUS.md` | S1 remainder: DBOS durable resume open |
| `D:\Cortex\CHANGELOG_DMS.md` | Notes DBOS resume deferred/skipped |
| `D:\Cortex\docs\dms\GATE_F8_PACKET.md` | Temporal mentioned only when run volume warrants |
| `D:\Cortex\pyproject.toml` | No `dbos` / `[agents]` extra yet — B1 adds it |
| `D:\Cortex\docs\research\respond_io_analysis.md` | Unrelated research sample; findings dir was otherwise empty |
| **Missing but cited:** `docs/research/findings/STREAMING_ORCH_2026.md` | BUILD_PLAN cites DBOS verdict here — **file not present in repo** as of 2026-07-22; this A1 doc re-grounds the verdict from current BUILD_PLAN + upstream DBOS docs |

External:

- https://docs.dbos.dev/python/integrating-dbos  
- https://docs.dbos.dev/python/tutorials/workflow-tutorial  
- https://docs.dbos.dev/python/tutorials/workflow-communication  
- https://docs.dbos.dev/explanations/comparing-temporal  
- https://pypi.org/project/dbos/2.28.0/

---

## Anti-scope / what NOT to build

- **Do not** adopt Temporal (or Cadence/Restate/Prefect) as the S1 primary runtime.
- **Do not** autonomous-publish: F5 / `requires_human` rail stays; approve API (or chat approve) required before `step_publish`.
- **Do not** put LLMs in detectors; keep `detectors.py` pure SQL.
- **Do not** add a cron daemon; schedules continue to tick from the existing orchestrator loop.
- **Do not** merge DBOS system tables into DuckDB lakehouse or invent a second agent registry.
- **Do not** build MCP agent surface, `@agent` chat dispatch, or F8 export tools inside the DBOS resume slice (those are separate S1/F8 items).
- **Do not** require Postgres for local/dev tests — SQLite default must work on Windows.
- **Do not** treat mid-step side effects as exactly-once without idempotent publish (overwrite same artifact path; guard ledger/registry updates).

---

## Suggested implementation slice for Cursor (smallest)

**Goal:** Unskip `test_workflow_resume_after_kill` only. No chat, no Temporal, no MCP.

1. **Dep:** add optional `[agents]` extra with `dbos==2.28.0`.
2. **Init:** small `packs/dms/agents/dbos_runtime.py` — `configure_and_launch()` reading `DBOS_SYSTEM_DATABASE_URL`, `run_admin_server=False` in tests; call from app lifespan **or** lazily on first agent run.
3. **Refactor `employee.py`:** wrap detect / draft / publish (and reject terminal) as `@DBOS.step`; one `@DBOS.workflow` that `recv`s approval; keep public `run_agent` / `approve_run` / `reject_run` signatures stable for `agent_routes.py` (store `workflow_id` on `dms_agent_runs` — additive column or encode in existing fields carefully).
4. **Idempotent publish:** if `artifact_path` already set / file exists with same content path, no-op success.
5. **Test:** implement chaos-lite resume test on tmp SQLite; remove skip.
6. **Docs touch (after code):** CHANGELOG_DMS + STATUS note; leave PARKING_LOT P3 “resume open” → closed only when B1 merges.

**Out of this slice:** `@agent` chat (`test_agent_chat_dispatch`), F8 tools, Postgres-only CI requirement, Temporal spike.

---

## One-line handoff for B1

> Pin `dbos==2.28.0`, SQLite system DB by default, map S1 to `@DBOS.workflow` + steps with `recv`/`send` at the approval gate, make publish idempotent, unskip `test_workflow_resume_after_kill`; Temporal remains docs-only scale-out.
