# STATUS.md

**Last updated:** 2026-08-06 | **Branch:** `chore/unblock-ci-and-estate-audit`
**History:** `CHANGELOG.md` (append-only) · **Map:** `docs/ACTIVE.md` · **Deferred:** `PARKING_LOT.md`
**Rule:** what is true *right now*, 60 lines max. Anything dated retires to `CHANGELOG.md`.

## Now

- **Answer path is manifest-grounded end to end.** The router states which tables its plan
  reads and abstains when the session never bound them (`Cortex#14`), and `/dms/query` no
  longer has an ungoverned executor — every read goes through `enforce_manifest` (`Cortex#6`).
  Both are implemented and pushed; **both await independent verify by a different run** (R-0003).
- **Contract 1.3.0 published** — additive `Answer.chart_spec`. 1.2.0 stays frozen on disk.
- **A read no longer takes the warehouse write lock.** The serving engine can be shared and
  scaled; this was the ceiling recorded on 2026-07-27.
- **One contract module identity** (`#5`), and mypy actually runs again — it had been
  exiting 2 without checking a single file.
- **The front page no longer claims Wasm sandboxing** (`#10`); the scaffold is deleted and
  a test now requires any isolation claim to have a real caller.
- **An absent inference backend is reported as absent.** `just_works` no longer returns
  `ok: True` for an Ollama that is not installed. Orchestration is ours and says so.

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
python -m mypy                              # 11 files; excludes build/ or it checks nothing
lint-imports                                # NOT python -m importlinter.cli — exits 0 running nothing
python scripts/check_versions.py            # engine 2.5.0 / contract 1.3.0, independent
python scripts/export_openapi.py --check    # needs .[full]
python scripts/check_contract_compat.py
```

The suite no longer needs the engine stopped. Tests that *write* the warehouse point at
their own `DMS_WAREHOUSE_DB`, and reads open read-only, so a serving engine on `:8010` no
longer locks them out. If a warehouse test starts failing with "used by another process",
it acquired a write lock it does not need — fix the test, do not stop the engine.

## Handoff

- North star: `docs/strategy/CORTEX_FINAL_GOAL.md`
- G2 enterprise loop (P21): `docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md`
- Continue prompts: `docs/dms/packets/NEXT_LANES.md`
- Truth map: `docs/dms/TRUTH_GROUND_MAP.md` · Research: `docs/research/findings/P0_INDEX.md`
- Shipped-layer table (V0-V1, F1-F8, O1-O7, G1-G2.2, E0): `CHANGELOG.md`
