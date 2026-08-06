# STATUS.md

**Last updated:** 2026-08-06 | **Branch:** `chore/unblock-ci-and-estate-audit`
**History:** `CHANGELOG.md` (append-only) · **Map:** `docs/ACTIVE.md` · **Deferred:** `PARKING_LOT.md`
**Rule:** what is true *right now*, 60 lines max. Anything dated retires to `CHANGELOG.md`.

## Now

Shipped this session, **all awaiting independent verify by a different run** (R-0003):

- **Answer path is manifest-grounded end to end** — the router states which tables it reads
  and abstains when the session never bound them (`#14`); `/dms/query` has no ungoverned
  executor left, every read goes through `enforce_manifest` (`#6`).
- **Contract 1.3.0** — additive `Answer.chart_spec`; 1.2.0 stays frozen on disk.
- **A read no longer takes the warehouse write lock** — the ceiling recorded 2026-07-27.
- **One contract module identity** (`#5`); mypy runs again after exiting 2 for who knows
  how long, checking nothing.
- **No Wasm sandboxing claim on the front page** (`#10`); scaffold deleted, claim ratcheted.
- **An absent backend is reported absent** — `just_works` no longer says `ok: True` for an
  Ollama that is not installed. Orchestration is ours and says so.

## Next

1. Independent verify + close `#14`, `#6`, `#5`, `#10` (R-0003 — a different run verifies).
2. Estate-audit bugs still open: `#7` EVAL-01, `#8` ANS-01, `#9` C2-01.
3. `EPIC-015` doc-RAG: `RAG-01` (dms#24) is the remaining ticket and lives in the DMS repo.

## Later

`EPIC-001` eval gate · `EPIC-002` contract identity + published wheel · `EPIC-006` C7 keyword
cascade (blocked on one real user, `#12`) · `EPIC-010` claim_n 47→310 (founder-attended, `#13`).

## Known broken / not green

- **`packs/dms/lakehouse/catalog.py` still imports duckdb** (2 sites), outside the sanctioned
  `CortexOS/execution`. Ratcheted by `tests/dms/test_c4_duckdb_boundary.py`; needs an
  architecture call on whether the lakehouse plane is a second legitimate opener.
- **26 ruff findings under `packs/dms/**`** — outside the documented gate scope, unaddressed.
- **`cortex-contract` is pip-installed editable at 1.1.0** while the tree is at 1.3.0. The
  editable finder points at the tree so imports are correct, but the installed metadata is
  stale. Reinstall before trusting a version read from `pip show`.

## Verify

```bash
python -m pytest tests/ -q                  # 1410 passed, 8 skipped — passes with the engine up
python -m ruff check CortexOS packages/cortex_contract scripts tests/packaging tests/contract
python -m mypy                              # 11 files; must exclude build/ or it checks nothing
lint-imports                                # NOT python -m importlinter.cli — exits 0 running nothing
python scripts/check_versions.py && python scripts/export_openapi.py --check  # needs .[full]
python scripts/check_contract_compat.py
```

The suite no longer needs the engine stopped: warehouse *writers* use their own
`DMS_WAREHOUSE_DB` and reads open read-only. A warehouse test failing with "used by another
process" is now a test taking a write lock it does not need — fix the test, not the engine.

## Handoff

North star `docs/strategy/CORTEX_FINAL_GOAL.md` · G2 loop / P21
`docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md` · continue prompts
`docs/dms/packets/NEXT_LANES.md` · truth map `docs/dms/TRUTH_GROUND_MAP.md` · research
`docs/research/findings/P0_INDEX.md` · shipped-layer table `CHANGELOG.md`
