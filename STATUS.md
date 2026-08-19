# STATUS.md

**Last updated:** 2026-08-20 | **Branch:** `chore/unblock-ci-and-estate-audit`
**History:** `CHANGELOG.md` · **Map:** `docs/ACTIVE.md` · **Deferred:** `PARKING_LOT.md` ·
**North star:** `docs/strategy/CORTEX_FINAL_GOAL.md` · `docs/dms/TRUTH_GROUND_MAP.md`
**Rule:** what is true *right now*, 60 lines max. Anything dated retires to `CHANGELOG.md`.

## Now

Independent verify (R-0003) put **six of seven** "shipped" tickets back on the board.

- **`#9` C2-01 closed** - gate watched going red, cold verdict == warm, all 8 crossings now
  behind `CortexOS/dms/semantic_port.py`, nothing added to `_C2_ALLOWLIST`.
- **`#8` ANS-01 fixed at the class** - the exclusion clause now ends *positively* at entities
  that resolve, so an unknown adverb cannot break it. 12/12 phrasings answer; gate proven red
  (10 failures) with the anchoring neutralised. Awaiting independent verify.
- **`#6`/`#14` are one defect.** `resolve_grant()` is now the single place deciding what a
  session may read, so `/dms/query` and `/mcp/call` cannot disagree with `/v1/contract/ask`.
  Degradation reaches the envelope as `grant_kind` / `granted_sources` (R-0011).
- **`#10` DOC-01** - an unbacked *container* claim had replaced the Wasm one; both gone. The
  gate that missed it now splits clauses at commas, and was watched failing. 24 zero-byte
  tracked modules deleted, incl. the empty `CortexOS/security/*`.

## Next

1. **`#14` step 1** - `route_to_metric` must return the tables its plan will read. Until then
   the grounding gate compares SQL tables to a grant that contains them, and is vacuous.
2. Independent verify + close `#8`, `#6`, `#10` - one run does not verify its own work.
3. `#5` CONTRACT-01 - four located gaps, no work landed. Blocks LINK 2 of `#11`.
4. `#36` ANS-02 and `#37` META-01 - new, both measured, neither routed to `prd-agent` yet.

## Known broken / not green

- **The P0 is still live on an unbound session.** `POST /dms/query` with no binding answers
  "total revenue in my uploaded file" with badge `governed_metric` and `revenue_myr:
  80375993.99` from the demo warehouse. The fallback is honest now (`local-self-issued`) but a
  wide grant honestly labelled is still wide. Refusing every unbound session breaks the demo
  (R-0005); answering under a success badge is the P0. Founder call.
- **`#7`: step 2 landed** (`_score_live` had three `# REVERT-A` lines short-circuiting its own
  fix), **but it covers only half of `ebd049b`.** Reintroducing the follow-up half turns it red
  (`wrong=6`); reintroducing the single-question half leaves it **green** - every
  `conversation` seed replays turns, so the form naming its own subject is covered by none.
  That form is also live-defective (`#36`).
- **`cortex-contract` is pip-installed at 1.1.0** against a 1.3.0 tree; `check_versions.py`
  ignores installed dist metadata, so `pip show` lies and the gate passes.
- 7 skipped tests, unjustified (R-0002); `packs/dms/lakehouse/catalog.py` imports duckdb
  outside `CortexOS/execution` (ratcheted).

## Verify

```bash
python -m pytest tests/ -q                  # 1503 passed, 7 skipped, 4 xfailed
PACK=dms python -m bench.corpus             # 406 total, 400 correct, 0 wrong, 0 regression
python -m ruff check CortexOS packages/cortex_contract scripts tests/packaging tests/contract
python -m mypy                              # 11 files
lint-imports                                # clear .grimp_cache AND .import_linter_cache first
python scripts/check_versions.py && python scripts/export_openapi.py --check
# pytest --timeout=N exits 0 HAVING RUN NOTHING when pytest-timeout is absent.
# Check a gate can fail before trusting it.
```
