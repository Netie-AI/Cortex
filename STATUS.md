# STATUS.md

**Last updated:** 2026-08-22 | **Branch:** `chore/unblock-ci-and-estate-audit`
**History:** `CHANGELOG.md` · **Map:** `docs/ACTIVE.md` · **Deferred:** `PARKING_LOT.md` ·
**North star:** `docs/strategy/CORTEX_FINAL_GOAL.md` · `docs/dms/TRUTH_GROUND_MAP.md`
**Rule:** what is true *right now*, 60 lines max. Anything dated retires to `CHANGELOG.md`.

## Now

Independent verify (R-0003) put **six of seven** "shipped" tickets back on the board.
- **`#5` CONTRACT-01 closed** - the second module identity is refused at *name binding*, so a
  notebook or `python -c` cannot build one. LINK 2 of `#11` (the wheel) is unblocked.
- **`#8` ANS-01 fixed at the class** - the exclusion clause ends *positively* at entities that
  resolve; 12/12 phrasings answer. Awaiting independent verify.
- **`#14` P0 closed** - the demo constraint was retired, so a served turn nothing grants
  refuses instead of widening. `require_grounding` is a parameter of `resolve_grant`: the
  transport states the policy, the engine keeps one authority. In-process callers keep the
  mint - removing it fails 161 tests, measured before deciding.
- **The platform runs.** `.claude/launch.json` named paths from another machine; it now starts
  the real factory on :8010, verified over HTTP rather than a TestClient.
- **`#9` C2-01 / `#10` DOC-01 closed** - 8 crossings behind `semantic_port.py`, cold == warm;
  an unbacked *container* claim had replaced the Wasm one, both gone, 24 empty modules deleted.
- **`#36` ANS-02 shipped, then repaired.** The first guard refused four working questions
  (`mean` matches inside "i mean") and missed the class by one synonym; its own R-0005
  control pinned a wrong answer (`#38`). Both fixed.

## Next

1. **`#42` SPACE-01** - a Space must grant its sources, as a signed manifest through the one
   registry. Not an unsigned mint in the engine; that is P0-DEMO-02 wearing a Space's name.
2. **`route_to_metric` must state the shape and tables its plan returns** - `#36`, `#38`, `#39`.
3. Independent verify + close `#8`, `#6`, `#10`, and the `#36` repair (R-0003).
4. `#38`, `#39`, `#37` measured but unrouted to `prd-agent`.

## Known broken / not green

- **A Space grounds nothing (`#42`).** `space_id` reaches the engine on every served door and
  scopes doc retrieval and session history, but nothing mints a grant from it, so a turn
  carrying only a Space abstains. With `#14` closed this is what stands between a buyer and an
  answer. The abstain deliberately does not say "select a Space" until it does.
- **`#7` half B now covered** - single-question seeds live in `semantic_ambiguity`, not
  `conversation`; a seed with no prior turn is not a conversation.
- **Two confidently-wrong answers live.** `#38` a grouped ranking returns the whole
  warehouse as one row; `#39` a question about *customers* returns SKUs and survives a correct
  grant. `#36` is mitigated by word lists, not closed. All want a plan-shape check.
- `netie` is installed at 0.1.0 against a 2.5.0 pyproject and nothing checks that
  (`cortex-contract` is now correctly 1.3.0, and `check_versions.py` would catch a relapse).
- 7 skipped tests, unjustified (R-0002); `packs/dms/lakehouse/catalog.py` imports duckdb
  outside `CortexOS/execution` (ratcheted).

## Verify

```bash
python -m pytest tests/ -q                  # 1551 passed, 7 skipped, 4 xfailed
PACK=dms python -m bench.corpus             # 434 total, 428 correct, 0 wrong, 0 regression
python -m ruff check CortexOS packages/cortex_contract scripts tests/packaging tests/contract
python -m mypy ; lint-imports   # clear .grimp_cache AND .import_linter_cache first
python scripts/check_versions.py && python scripts/export_openapi.py --check
# pytest --timeout=N exits 0 HAVING RUN NOTHING when pytest-timeout is absent (R-0007).
```
