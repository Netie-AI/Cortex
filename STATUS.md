# STATUS.md

**Last updated:** 2026-08-06 | **Branch:** `chore/unblock-ci-and-estate-audit`
**History:** `CHANGELOG.md` (append-only) · **Map:** `docs/ACTIVE.md` · **Deferred:** `PARKING_LOT.md`
**Rule:** what is true *right now*, 60 lines max. Anything dated retires to `CHANGELOG.md`.

## Now

Shipped this session, **all awaiting independent verify by a different run** (R-0003):

- **Answer path is manifest-grounded end to end** — the router names the tables it reads and
  abstains when the session never bound them (`#14`); `/dms/query` has no ungoverned executor
  left (`#6`). **Contract 1.3.0** adds `Answer.chart_spec`.
- **A read no longer takes the warehouse write lock** — the ceiling recorded 2026-07-27.
- **One contract module identity** (`#5`); mypy runs again after exiting 2, checking 0 files.
- **No Wasm sandboxing claim on the front page** (`#10`); scaffold deleted, claim ratcheted.
- **Absent backends are reported absent** — `just_works` no longer claims an uninstalled
  Ollama works. Orchestration is ours and says so.
- **OSR can reach `known`** — the band letting Pointer reuse a proven plan. The scoreboard was
  empty (`arch_families: 0`) because 327 runs of a routine pinned to an adapterless preset
  starved it. Stubs unselectable, permanent faults park on run 1, `known` works on the
  existing feature hash — **no JEPA rewrite needed**, and that hash is no longer misnamed
  "JEPA". KB `F-0008`.

## Next

1. Independent verify + close `#14`, `#6`, `#5`, `#10` (R-0003 — a different run verifies).
2. Estate-audit bugs still open: `#7` EVAL-01, `#8` ANS-01, `#9` C2-01.
3. `EPIC-015` doc-RAG: `RAG-01` (dms#24) is the remaining ticket.

## Known broken / not green

- **`packs/dms/lakehouse/catalog.py` still imports duckdb** (2 sites) outside sanctioned
  `CortexOS/execution` — ratcheted; needs a call on whether the lakehouse is a 2nd opener.
- **`cortex-contract` is pip-installed editable at 1.1.0** against a 1.3.0 tree; imports
  resolve, `pip show` lies.
- **26 ruff findings under `packs/dms/**`**, outside the gate scope. **`langgraph` /
  `langchain` have no adapter** and are unselectable; real representation learning in the
  decision layer is unrouted — see `F-0008`.

## Verify

```bash
python -m pytest tests/ -q                  # ~1430 passed, 8 skipped — passes with the engine up
python -m ruff check CortexOS packages/cortex_contract scripts tests/packaging tests/contract
python -m mypy                              # 11 files; must exclude build/ or it checks nothing
lint-imports                                # NOT python -m importlinter.cli — exits 0 running nothing
python scripts/check_versions.py && python scripts/export_openapi.py --check  # needs .[full]
python scripts/check_contract_compat.py
```

Warehouse *writers* use their own `DMS_WAREHOUSE_DB` and reads open read-only, so the suite
no longer needs the engine stopped. A warehouse test failing with "used by another process"
is a test taking a write lock it does not need — fix the test, not the engine.

## Handoff

North star `docs/strategy/CORTEX_FINAL_GOAL.md` · G2 loop / P21
`docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md` · continue `docs/dms/packets/NEXT_LANES.md`
· truth map `docs/dms/TRUTH_GROUND_MAP.md` · shipped layers `CHANGELOG.md`
