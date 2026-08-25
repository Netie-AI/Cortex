---
keywords: [workflows, tasks, sqlite, wal, disk-io, 500, snapshot]
main_idea: GET /api/workflows/tasks 500s when workflow_store SQLite hits Windows disk I/O (wedged WAL in a long-lived uvicorn). Activity isolates that error; the tasks route did not. Retry WAL then DELETE journal. Restart the wedged :8010 process — new processes can open the same DB.
---

# GET /api/workflows/tasks HTTP 500

## Main idea

`GET /api/engine/activity` already reported `workflows.error = OperationalError: disk I/O error` (fault-isolated). `GET /api/workflows/tasks` calls `workflow_runner.snapshot()` -> `workflow_store.list_runs()` with no isolation, so the same SQLite I/O becomes HTTP 500. OST/OpenMW treat that as soft-fail and retry; `/api/ai/mode` and `/api/ai/config` 200s are unrelated.

Live pid was Python 3.14 uvicorn in `D:\Cortex` (ANS owns :8010). The DB at `D:\Cortex\data\workflows\runs.db` opened clean from a new process (integrity ok, 9 runs). The 500 was in-process WAL/shm I/O, not a bad request shape.

Crew gate after the 500: `ask_agent` treated a target's `REPORT` as "did not answer" even though the switchboard had already correlated it. That is a dropped answer on the wire the operator reads. Stamp `reply_to` on REPORT too; say `answered:` for any answering kind.

## Golden rule

> Workflow panel reads go through `_conn()` with WAL retry then DELETE journal. A 500 on `/api/workflows/tasks` is a store I/O failure, not a client bug. Do not swallow it into empty 200s. Restart a wedged engine; do not rewrite OST.

## Verify

```bash
cd D:\Cortex-crew
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_workflow_runner.py tests/dms/test_agent_engine_routes.py tests/test_crew -q
# live
# Invoke-WebRequest http://127.0.0.1:8010/api/workflows/tasks
```
