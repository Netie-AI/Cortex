# C7-02: Manifest before EXPLAIN on every L2 candidate

- Date: 2026-09-04
- Keywords: C7-02, C7, L2, enforce_manifest, EXPLAIN, ManifestError, PathNotAllowed, refused, sql_validate_gate, attempt_l2
- Main idea: When L2 generates SQL for a grounded session, `run_gate` calls `enforce_manifest` after sqlglot and before EXPLAIN. ManifestError fails that candidate without EXPLAIN, `attempt_l2` signals `refused` / `L2_MANIFEST:`, and `answer()` emits route/layer/badge=refused (not SESSION abstain). EXPLAIN sees only post-enforce SQL.
- Path: this file

## PREFLIGHT

HIT. reuse: `docs/subagents_findings/2026-09-04_c7-design-and-shadow-mode.md`. spawn: skip (executor).

## Golden rules

1. Never weaken `CortexOS/execution/manifest.py` refusals. Enforcer before EXPLAIN.
2. CortexOS must not statically import `packs.*`. L2 stays on `CortexOS.dms.l2_generation`.
3. ManifestError is refused, not `_abs()` SESSION-adjacent abstain.
4. Customer-envelope assertions (answer text + rows + badge), not SQL-only gates.
5. Do not set `DMS_L2_ENABLED` as the serve default. Do not implement C7-03.

## What shipped

- `run_gate` / `gate_with_retry` take optional `verified`. After sqlglot, `enforce_manifest`; on `ManifestError` return `passed=False` with `MANIFEST:{Type}:{code}` and skip EXPLAIN. Then EXPLAIN the enforced SQL only. `max_retries=2` stays; violations feed the next candidate.
- `attempt_l2` always opens the warehouse connection when it will EXPLAIN (including when `verified` is set). Gate exhaustion from ManifestError returns `L2Attempt(refused=True, reason=L2_MANIFEST:...)`.
- `answer()` maps that to `_abstain_refused` (route/layer/badge=refused).

## Verify

```
ruff check CortexOS/dms/sql_validate_gate.py CortexOS/dms/l2_generation.py CortexOS/dms/answer_engine.py tests/dms/test_sql_validate_gate.py tests/dms/test_c7_02_manifest_before_explain.py
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/dms/test_sql_validate_gate.py tests/dms/test_c7_02_manifest_before_explain.py tests/dms/test_c7_l2_shadow.py tests/test_execution/test_submit_c4.py -q --tb=short
```

Does not prove: C7-03 plausibility, L1 cutover, live FreeRoute quality, or that a later L2 candidate that passes enforce is served when `require_grounding` has empty `planned_tables`.
