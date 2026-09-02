---
id: 2026-08-27_cursor_dms-local-ci-bootstrap
source: cursor
date: 2026-08-27
operator: founder-laptop
prompt_used: skill_distill/prompts/ASK_CURSOR.md
distill_trace: skill_distill/DISTILL.md
status: raw
---

# DMS local bootstrap and CI honesty

A Claude Code session built a venv, hunted cortex-contract on PyPI (miss),
found the real wheel in the Cortex tree, then lost the pytest log to a Git
Bash path bug (`$TMPDIR/pytest_full.txt` -> `/pytest_full.txt`). First
visible run printed a block of `E` then many dots. `--timeout=300` is not
a pytest flag in this repo.

This capture is the surviving command list so the next agent does not
re-derive it.

## Raw answer

Observed 2026-08-27 on `E:\DMS` (branch `cursor/status-f71-merge-wave`):

- `E:\DMS\.venv\Scripts\python.exe` is Python 3.13.14, pytest 9.1.1.
  Re-run 2026-08-27 with skip env: **458 passed, 31 skipped, 0 failed**
  in 3m48s. The 31 skips are `tests/control_plane` (no Postgres). The
  previous `EEEE` block was those 31 tests erroring without the skip.
- `cortex-contract==1.2.0` is installed as a site-package (real wheel).
  It is **not** on PyPI. CLAUDE.md rule 2: never editable-install into
  the Cortex tree. Build from `E:\Cortex\packages\cortex_contract`.
- `D:\Cortex` does **not** exist on this machine. `E:\Cortex` does.
  `scripts/ci_local.sh` defaults `CORTEX_CONTRACT_SRC` to
  `D:/Cortex/packages/cortex_contract` and will fail closed here unless
  that env is set.
- Docker Desktop is down (`dockerDesktopLinuxEngine` pipe missing).
  `ci_local.sh` and `tests/control_plane` both need it.
- `tests/control_plane/conftest.py` **errors** (pytest `E`, not skip)
  when Postgres is unreachable unless `DMS_SKIP_CONTROL_PLANE_TESTS=1`.
  That is the `EEEE...` block at the start of a naive `pytest tests`.
  CI already sets the skip (`.github/workflows/ci.yml`).
- `pytest-timeout` is not in `pyproject.toml` optional-deps. Do not pass
  `--timeout`.
- GitHub CI on push/PR already runs ruff, mypy, import-linter, and
  `pytest tests/` with the skip. It does **not** run
  `score_answers.py --oracle-only`, `verify_freeform_demo.py --self-check`,
  or `ontology_bench.py` (STATUS: Bench in CI = No; PRD F42 -> EPIC-018
  #35 CI-ACCURACY checkbox, still open).
- SCORE-03 (`Netie-AI/dms#42`) is CLOSED; epic #35 body still shows it
  unchecked (parent not updated).
- `DMS_DEMO_FALLBACK=1` is a lying affordance (CLAUDE.md 11). Prefer 0.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| cortex-contract is a local wheel, never PyPI, never editable Cortex | observed | high | rule |
| Naive pytest EEEE is control_plane without Postgres and without the skip env | observed | high | rule |
| Do not pass pytest --timeout; package is not a dep | observed | high | rule |
| Git Bash `$TMPDIR/file` can resolve to `/file` and fail with Permission denied | observed | high | rule |
| ci_local.sh D:/Cortex default is wrong on this laptop (E:/Cortex) | observed | high | rule |
| GitHub CI pytest is already on push/PR; missing piece is oracle/accuracy gates (F42) | docs | high | parking |
| Auto-run for agents = alwaysApply Cursor rule + STATUS Direct interact, not a sixth test runner | inferred | high | rule |

## Action YAML

```yaml
build_now:
  - DMS .cursor/rules/local-test-bootstrap.mdc alwaysApply
  - next agent: pytest with DMS_SKIP_CONTROL_PLANE_TESTS=1 unless hostdb is up
  - write logs under E:\DMS\.tmp\ not $TMPDIR
park:
  - GitHub CI oracle job until EPIC-018 CI-ACCURACY is ticketed (F42)
  - pytest-timeout extra dep (not needed; CI has job timeout-minutes)
  - starting Docker Desktop from the agent (R-0015)
tests:
  - pytest tests -q with skip env
  - python scripts/score_answers.py --docs tests/fixtures/hostile_score --oracle-only
  - python scripts/verify_freeform_demo.py --self-check
```

## Netie implications

- Build now: inject the bootstrap into Cursor so Claude does not hunt PyPI
  or redirect to `/pytest_full.txt`.
- Park (condition): EPIC-018 CI-ACCURACY ticket exists and names the
  stack-free oracle job separately from live ask (R-0011).
- Tests required: local pytest with skip env; oracle-only scripts.

## Citations

- distill: skill_distill/captures/2026-08-27_cursor_dms-local-ci-bootstrap.md
- E:\DMS\tests\control_plane\conftest.py
- E:\DMS\.github\workflows\ci.yml
- PRD-001 F42 -> Netie-AI/dms#35
