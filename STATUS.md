# STATUS.md

**Last updated:** 2026-08-21 | **Branch:** `chore/unblock-ci-and-estate-audit`
**History:** `CHANGELOG.md` · **Map:** `docs/ACTIVE.md` · **Deferred:** `PARKING_LOT.md` ·
**North star:** `docs/strategy/CORTEX_FINAL_GOAL.md` · `docs/dms/TRUTH_GROUND_MAP.md`
**Rule:** what is true *right now*, 60 lines max. Anything dated retires to `CHANGELOG.md`.

## Now

Independent verify (R-0003) put **six of seven** "shipped" tickets back on the board.
- **`#5` CONTRACT-01 closed** - the second module identity is refused at *name binding*, so a
  notebook or `python -c` cannot build one. LINK 2 of `#11` (the wheel) is unblocked.
- **`#8` ANS-01 fixed at the class** - the exclusion clause ends *positively* at entities that
  resolve; 12/12 phrasings answer. Awaiting independent verify.
- **`#6`/`#14` are one defect.** `resolve_grant()` is the single place deciding what a session
  may read; degradation reaches the envelope as `grant_kind` / `granted_sources` (R-0011).
- **`#9` C2-01 closed** - 8 crossings behind `semantic_port.py`, cold verdict == warm, gate
  watched going red. **`#10` DOC-01** - an unbacked *container* claim had replaced the Wasm
  one; both gone, gate now splits clauses at commas, 24 zero-byte modules deleted.
- **`#36` ANS-02 shipped, then repaired.** The first guard refused four working questions
  (`mean` matches inside "i mean") and missed the class by one synonym; its own R-0005
  control pinned a wrong answer (`#38`). Both fixed.

## Next

1. **`#14` step 1** - `route_to_metric` must state the shape and tables its plan returns.
   `#36`, `#38` and `#39` all need it; until then the grounding gate is vacuous.
2. Independent verify + close `#8`, `#6`, `#10`, and the `#36` repair (R-0003).
3. `#38` ANS-03, `#39` ANS-04, `#37` META-01 - all measured, none routed to `prd-agent` yet.
4. Independent verify of the `#36` repair - I wrote it, so it does not count (R-0003).

## Known broken / not green

- **The P0 is still live on an unbound session.** `POST /dms/query` with no binding answers
  "total revenue in my uploaded file" with badge `governed_metric` and `revenue_myr:
  80375993.99` from the demo warehouse. The fallback is honest now (`local-self-issued`) but a
  wide grant honestly labelled is still wide. Refusing every unbound session breaks the demo
  (R-0005); answering under a success badge is the P0. Founder call.
- **`#7` half B now covered** - single-question seeds live in `semantic_ambiguity`, not
  `conversation`; a seed with no prior turn is not a conversation.
- **Two confidently-wrong answers live and unfixed.** `#38` a grouped ranking returns the whole
  warehouse as one row; `#39` a question about *customers* returns SKUs and survives a correct
  grant. `#36` is mitigated by word lists, not closed. All need `#14` step 1.
- **`cortex-contract` is pip-installed at 1.1.0** against a 1.3.0 tree. `check_versions.py`
  now catches it and exits 1 - a true positive. Fix: `pip install --no-deps
  --force-reinstall -e packages/cortex_contract`. `netie` is likewise installed at 0.1.0
  against a 2.5.0 pyproject, and nothing checks that.
- 7 skipped tests, unjustified (R-0002); `packs/dms/lakehouse/catalog.py` imports duckdb
  outside `CortexOS/execution` (ratcheted).

## Verify

```bash
python -m pytest tests/ -q                  # 1550 passed, 7 skipped, 4 xfailed
PACK=dms python -m bench.corpus             # 434 total, 428 correct, 0 wrong, 0 regression
python -m ruff check CortexOS packages/cortex_contract scripts tests/packaging tests/contract
python -m mypy ; lint-imports   # clear .grimp_cache AND .import_linter_cache first
python scripts/check_versions.py && python scripts/export_openapi.py --check
# pytest --timeout=N exits 0 HAVING RUN NOTHING when pytest-timeout is absent (R-0007).
```
