# H2-LEGAL-CLAMP: token-level legal terms; floor above DSL max_tier fails closed (#251)

- **Date:** 2026-09-24
- **Epic:** #249 EPIC-HARDEN-2 (closes the residual noted as ASSUMED in `2026-09-23_gh-02.md`)
- **Keywords:** judgment model, tokenizer, snake_case, camelCase, vip floor uplift, legal floor, vip floor, max_tier, bounded_tier, fail closed, word boundary, false positive corpus, model router, cost ledger
- **Main idea:** `ModelRouter.route` clamped every judged tier to the node's `max_tier`, so the GH-02 legal/financial T2 floor was silently served at T1 in a `max_tier=T1` node. The floor now refuses with a typed error (`TierFloorAboveCap`) that names the floor, the judged tier and the cap; the adapter is never called and the existing executor error path writes the ledger row. To make that safe, `LEGAL_TERMS` match whole words (case-insensitive) instead of substrings, so `space`, `illegally`, `loaner` and `contractor` no longer floor ordinary prompts.
- **Verify:** `python -m pytest tests/test_execution/test_legal_clamp.py -q -p no:cacheprovider` (280 tests) then `python -m pytest tests/ -q -p no:cacheprovider`
- **Does not prove:** anything about production traffic (no sample; the 30-prompt corpus is a regression guard, not a false-positive rate). No live kev backend.

## Expected vs actual

Expected (issue #251 acceptance):
- Legal/financial terms match as whole words or phrases, case-insensitively.
- A judged tier above `max_tier` caused by a deterministic safety floor (legal/financial T2, VIP) is refused with a typed error, no adapter call, and an error ledger row from the existing path.
- A judged tier above `max_tier` for a non-floor reason (context-size heuristic, backend choice) keeps the clamp.
- With no floor the routing is unchanged.

Actual before (VERIFIED at 275fe13 by `scratchpad/probe_base_router.py`, a one-node `llm_judged` DAG with `default_tier=T1, max_tier=T1` through `run_dag`):

```
'review this loan agreement': served tier=T1; adapter_calls=1; ledger=[('T1', 'ok', 'qwen2.5-7b-instruct'), ...]
'book the conference space': served tier=T1; adapter_calls=1; ledger=[('T1', 'ok', 'qwen2.5-7b-instruct'), ...]
```

The loan review reached the T1 model with a green `ok` ledger row. And on the base, `JudgmentModel().rules_decide("book the conference space")` returned T2 with reason `financial/legal terms detected` because `spa` is a substring of `space`; 26 of the 30 corpus prompts were floored the same way.

Actual after (same probe, same checkout):

```
'review this loan agreement': raised TierFloorAboveCap: legal/financial floor requires T2 for request 'j1' but the node caps max_tier at T1; refusing to serve below the floor; adapter_calls=0; ledger=[('T2', 'error', 'qwen2.5-32b-instruct')]
'book the conference space': served tier=T1; adapter_calls=1; ledger=[('T1', 'ok', 'qwen2.5-7b-instruct'), ...]
```

## Verifier findings on d1d4d10 (round 2)

Three ways the legal floor was still served below the cap in a `max_tier=T1` node, each reproduced with `/tmp/h2probe/probe.py` and `/tmp/h2probe/dag.py` (one `llm_judged` node, prompt `review this loan agreement`) before the fix:

| Finding | Before (VERIFIED at d1d4d10) | Cause | After |
|---|---|---|---|
| kev backend already at or above T2 | `backend T2 legal: tier=T1 refused=False`; DAG `kev_T2 SERVED adapter_calls=1 [('T1','ok')]` | `apply_rules_floor` returns `None` when `chosen >= floor`, so `decide()` built the decision with `floor=None` and the router clamped | `kev_T2 RAISED TierFloorAboveCap ... adapter_calls=0 [('T2','error')]`; same for a T3 choice |
| backend abstains | `backend abstain legal: tier=T1 refused=False reason=... rules fallback: ...` | the abstain branch rebuilt `JudgmentDecision` without `floor=rules.floor` | refused, `[('T2','error')]` |
| birthday shadows legal | `birthday SERVED adapter_calls=1 [('T1','ok')]` for `birthday card, then review this loan agreement` | `rules_decide` returned on the birthday branch (`floor=None`) before the legal check; `apply_rules_floor` chose the birthday floor the same way | refused, `[('T2','error')]`; birthday-only prompts keep the clamp |

Fix, all in `judgment_model.py` plus a four-line change in `model_router.py`:

- `JudgmentDecision.floor_tier: Tier | None`, the tier the floor requires. `tier` may sit above it (a backend chose T3); the router refuses when `floor_tier` (not `tier`) exceeds the cap, and clamps otherwise. So a T3 backend on a legal prompt in a `max_tier=T2` node is served at T2 (the cap satisfies the floor) instead of a false refusal; that case is asserted.
- `JudgmentModel.safety_floor_for(req)` names the legal floor from the request's content alone, whatever tier was chosen. `decide()` stamps it on every backend-served decision, including when `apply_rules_floor` returns `None`; the abstain branch passes `floor`/`floor_tier` through from the rules fallback.
- `rules_decide`: the birthday branch stamps `floor=LEGAL_FLOOR, floor_tier=T2` when legal terms co-occur (tier stays T3, reason gains `; financial/legal terms detected`). Birthday alone leaves `floor=None`.
- The refused `RoutedModelCall` and the ledger row are at the floor tier (T2), and `TierFloorAboveCap.judged_tier` is that required tier, so the message says `legal/financial floor requires T2` even when the backend chose T3.

13 new tests: DAG refusals for `FixedChoiceBackend(T2)`, `FixedChoiceBackend(T3)`, an abstaining `UniformScoreBackend`, birthday+legal with rules and with a T3 backend (each asserting `TierFloorAboveCap` fields, `spy.requests == []`, one `status=error` row at T2 with the floor named); `birthday+legal` decision carries the floor on four judgment paths; backend T2/T3 on a legal prompt keeps the choice and the floor with the reason string unchanged; T3 backend in a T2 node served at T2; birthday-only prompt in a T1 node served at T1.

Fail on base (d1d4d10 `judgment_model.py` + `model_router.py`, new test file): 11 failed, 168 passed. The two new tests that pass on d1d4d10 are the no-false-refusal guards (`test_dag_t3_backend_on_legal_prompt_in_t2_node_is_clamped_to_t2_not_refused`, `test_dag_birthday_only_prompt_in_t1_node_keeps_the_clamp`); they guard the fix from over-refusing rather than reproduce a finding. On 275fe13 sources the module does not collect (`FloorRefusalAdapter` absent), as before.

## Verifier findings on a485f82 (round 3)

| Finding | Expected | Actual at a485f82 (VERIFIED by the new tests on a485f82 sources) | Root-cause class | After |
|---|---|---|---|---|
| 1. `_` is a regex word character, and the text was lowercased before matching | `loan_agreement`, `stamp_duty_calc`, `LoanAgreement`, `SpaForm`, `{"nricNumber": ...}` floor to T2 (the substring base floored them) | `rules_decide` returned T1 / `floor=None`; in a `max_tier=T1` node the prompt was **served** at T1. The floor failed OPEN on structured prompts, a regression against 275fe13 | precision fix that silently narrowed recall: "word boundary" was defined by the regex engine's `\w`, not by what a term looks like in identifiers | floored; refused in a T1 node with zero adapter calls and a T2 error row |
| 2. VIP `floor_tier` was the final VIP tier | a large VIP prompt (heuristic T2, VIP bump to T3) in a `max_tier=T2` node is clamped to T2 | `floor_tier=T3`, so the router **refused** (`TierFloorAboveCap`) a request the cap satisfies | floor conflated with the final tier: the heuristic uplift was stamped as if a safety rule forced it | `floor_tier=T2` (`VIP_FLOOR_TIER`); served at T2 with an `ok` row; a T1 cap still refuses and names T2 |

Fix:

- `legal_tokens(text)` (module function) splits on every non-alphanumeric character (whitespace, punctuation, `_`, `-`, `.`, quotes) and on case/digit boundaries with one regex: `[A-Z]{2,}s(?![a-z])` (pluralised acronym `SPAs`), `[A-Z]+(?=[A-Z][a-z])` (acronym before a PascalCase word, `SPAForm` -> `spa`, `form`), `[A-Z]?[a-z]+`, `[A-Z]+`, `[0-9]+`, `[^\W\d_]+` (other letters); then lowercases. A single-word term matches a token, a phrase matches consecutive tokens (`stamp duty` matches `stamp_duty`, `stampDuty`, `stamp-duty`, `stamp   duty`), and the last token may carry a plain plural `s`. Term phrases are tokenised the same way and cached per `LEGAL_TERMS` tuple.
- Every caller now passes `req.content` (original case), not `req.content.lower()`, so camelCase boundaries survive. Lowercased input still matches separator-delimited terms.
- `JudgmentModel.LEGAL_FLOOR_TIER = VIP_FLOOR_TIER = Tier.T2`. The VIP branch keeps `tier` (T2 or T3, including the context-size/retry uplift) and stamps `floor_tier=VIP_FLOOR_TIER`. The router is unchanged in logic (it already refuses on `floor_tier`, not `tier`); only its comment now says so. Birthday stays a quality floor with the clamp (`test_tier_routing.py::test_router_respects_max_tier` asserts that, unmodified), so it is not a refusal floor.

Tests (101 new parametrised cases, 280 in the file):

- `STRUCTURED_LEGAL_CORPUS` (23 prompts: snake, camel, Pascal, acronym+Pascal, pluralised acronym, kebab, dotted, digit-glued, JSON keys) asserted three ways: rules floor to T2, router refusal in a T1 node (`FloorRefusalAdapter`, reason `refused: legal/financial floor requires T2`), and a T0 kev backend floored to T2. Three DAG refusals (`loan_agreement_v2`, `LoanAgreement`, `{"stamp_duty_calc": 1}`) assert the typed error, `spy.requests == []` and one `status=error` T2 ledger row.
- Every `LEGAL_TERMS` entry matches as `x_<term>_id`, `get<Term>Value` and a JSON key, and not glued inside a longer word.
- The issue's boundary list: `SPA`, `spa_form`, `SpaForm`, `legal`, `loan`, `contract` match; `space`, `spacecraft`, `illegal`, `loaner`, `contractor` do not.
- `NON_LEGAL_CORPUS` is now 35 prompts. `spa_day is a calendar tag` was removed: it asserted the very fail-open behaviour finding 1 names (`spa_day` is `spa` + `day`). Six identifier-shaped non-legal prompts were added (`spacecraft_launch`, `illegalMove`, `contractorName`, `loanerCar`, `SpaceStation`, `paralegal_team`), and each prompt is now checked in original case as well as lowercased.
- VIP: large prompt decides `tier=T3, floor_tier=T2`; VIP with a prior failure decides `tier=T3, floor_tier=T2`; DAG large VIP prompt in a T2 node served at T2 (`ok` row, spy called once with the T2 model); in a T1 node refused with `judged_tier=T2` and message `vip floor requires T2`.

Fail on previous (a485f82 `judgment_model.py` + `model_router.py`, new test file): **74 failed, 206 passed**. Failing: `test_structured_legal_prompt_is_floored_to_t2` (19/23), `test_structured_legal_prompt_in_t1_node_is_refused` (19/23), `test_structured_legal_prompt_floors_a_t0_backend` (19/23), `test_dag_structured_legal_prompt_in_t1_node_is_refused` (3/3), `test_every_legal_term_matches_in_snake_camel_and_json` (9/9), `test_token_match_boundaries_named_in_the_issue`, `test_vip_large_prompt_decision_keeps_t3_but_floor_is_t2`, `test_vip_prior_failure_decision_floor_is_t2`, `test_dag_vip_large_prompt_in_t2_node_is_clamped_to_t2_not_refused`, `test_dag_vip_large_prompt_in_t1_node_is_refused_at_the_vip_floor`. Passing on a485f82 by design: the four structured prompts the word-boundary regex already handled (`the SPAs are attached`, `loan-agreement.pdf`, `config.legal.review = true`, `doc.agreement.signed`) are kebab/dotted/plural guards against regressing them, and the six new no-false-positive entries guard the tokenizer from over-splitting.

## Repro

```
# from the worktree root
python -m pytest tests/test_execution/test_legal_clamp.py -q -p no:cacheprovider
```

Or the probe above: swap `CortexOS/execution/model_router.py` for `git show 275fe13:CortexOS/execution/model_router.py`, run the probe, restore.

## What changed

| Piece | Where |
|---|---|
| `JudgmentDecision.floor: str | None = None`, the name of the deterministic safety floor that produced the tier. `JudgmentModel.LEGAL_FLOOR`, `VIP_FLOOR`, `SAFETY_FLOORS`. `rules_decide` sets it on the legal branch, on the VIP bump, and on the birthday branch when legal terms co-occur; `decide()` sets it on every backend-served decision whose content triggers it (`safety_floor_for`) and passes it through the abstain fallback (round 2, below). Birthday alone and the T0 pin leave it `None`. `floor_tier` carries the tier the floor requires. | `CortexOS/routing/judgment_model.py` |
| (Round 1; superseded in round 3 by token matching, see above.) `_contains_legal_terms` used one compiled, cached, case-insensitive pattern: `(?<!\w)(?:term|...)s?(?!\w)`. Phrases match across any whitespace run. A plain plural (`contracts`) still matches; hyphenated compounds (`sub-contract`) still match, which is the fail-closed side. `spa` no longer matches `space`, `spatula`, `spare`, `spanish`; `legal` no longer matches `illegal`, `paralegal`, `legalese`; `loan` no longer matches `loaner`, `loaned`, `loanwords`; `contract` no longer matches `contractor`, `contractual`, `contraction`. | `CortexOS/routing/judgment_model.py` |
| `TierFloorAboveCap(RuntimeError)` with `request_type`, `floor`, `judged_tier`, `max_tier` and a message naming all three tiers and the floor. `FloorRefusalAdapter`: `complete()` raises the refusal, `cost_myr()` is 0. `route()`: when `decision.floor` is set and the judged tier is above `max_tier`, returns a `RoutedModelCall` at the judged tier whose adapter is the refusal adapter and whose reason starts with `refused:`; otherwise `bounded_tier` exactly as before. | `CortexOS/execution/model_router.py` |
| 166 tests: three DAG refusals (rules legal, VIP, kev T0 backend floored to T2) asserting the typed error fields, zero spy calls, and the `status=error` ledger row with the floor name in `error`; `book the conference space` served at T1 in both a T1-capped and an uncapped node; large-context heuristic T2 clamped to T1; backend T3 clamped to T1; birthday keeps the clamp; 40 (prompt, bounds) cases equal to `bounded_tier`; a 30-prompt no-false-positive corpus asserted three ways (rules decision, T1-capped router, uncapped router); 15 positive prompts incl. case, punctuation, plural, hyphen and multi-space phrase; every `LEGAL_TERMS` entry as a whole word and glued; `estimate_node_cost` on a refused node is 0. | `tests/test_execution/test_legal_clamp.py` |

Not touched: `CortexOS/decision/**`, `executor.py`, `dag_runner.py`, `CortexOS/security/**`, `tests/contract/**`, `tests/invariants/**`, `.importlinter`, `contract/**`.

### Why the refusal is an adapter and not a raise in `route()`

`invoke_routed_completion` (executor.py:106) calls `router.route()` before any ledger write, and its only `except Exception` wraps `adapter.complete`. A raise from `route()` would refuse with no ledger row, and `executor.py` is outside this ticket's write targets. Handing back an adapter that raises on `complete` reuses the existing error path unchanged: redaction runs, the cost gate sees a 0 projection, `complete` raises, the executor writes `status=error` with the message and re-raises, `run_dag` emits `node_error` and propagates. The DAG pre-gate (`_price_one_call`) also routes and prices 0 for the refused node, so the refusal is not turned into a cost error first.

### Which floors fail closed

Only `SAFETY_FLOORS = {legal/financial floor, vip floor}`, as the issue names. The birthday floor is a quality floor and keeps the clamp; `tests/test_execution/test_tier_routing.py::test_router_respects_max_tier` asserts that a `birthday_rapport` request in a `max_tier=T2` node is served at T2 and still passes unmodified. `prior_tier_failures` is treated as heuristic and keeps the clamp.

## Root-cause class

Two instances of "policy expressed as a local step instead of an end-to-end post-condition":

1. The floor was applied inside `JudgmentModel` and then discarded by an unconditional clamp one call later. The decision carried no signal about why the tier was what it was, so the router could not tell a safety floor from a heuristic.
2. The term match was a substring test written for a handful of tokens; the fail-closed change made its precision matter, and the precision was low (26/30 false floors on the corpus).

## Invariant applied

A deterministic safety floor is never served below. `route()` checks `TIER_ORDER[decision.tier] > TIER_ORDER[req.max_tier]` only when `decision.floor` is set; the served tier is either at or above the floor, or nothing is served. Fail closed on the match side too: plurals and hyphenated compounds still floor. And a control that blocks legitimate work is a failure, so the 30-prompt corpus is asserted not floored on the same code path.

## Verified vs assumed

VERIFIED (round 3, worktree on a485f82):
- Full suite: 2641 passed, 13 skipped, 4 xfailed; rc=0 checked directly (+101 new).
- `ruff check` on the three changed files: rc=0. `lint-imports`: 3 kept, 0 broken, rc=0. `tests/contract`: 95 passed, rc=0. `check_versions.py`: OK, rc=0.
- `mypy` on the two changed modules: no errors reported in `judgment_model.py` or `model_router.py`.
- GH-02 (`test_judgment_rules_floor.py`), `test_tier_routing.py` and `tests/test_decision` (KEV-DECIDE, KEV-SHADOW) pass unmodified.
- The fail-on-previous counts above (74 failed on a485f82 sources, source files restored afterwards, `git diff` confirmed).

ASSUMED / known (round 3):
- Case-boundary splitting has its own false positives: `spaCy` (the NLP library) tokenises to `spa`, `cy` and floors. Chosen on the fail-closed side because `SpaForm` must floor and the two are indistinguishable by shape. Not in any corpus; no production sample to weigh it.
- Non-ASCII letters are not case-split (`[A-Z]`/`[a-z]` are ASCII); a legal term glued to an accented letter (`loané`) splits into `loan`, `é` and floors. Fail-closed side.
- `birthday` detection is still a substring test (`"birthday" in text`); out of scope, and it only ever raises a tier.

VERIFIED (round 2, worktree on d1d4d10):
- Full suite: 2540 passed, 13 skipped, 4 xfailed (+13 new). rc=0 checked directly.
- `ruff check` on the three changed files: clean. `ruff format --check` reports the same three files as it did at d1d4d10 (pre-existing, not run in CI).
- `mypy` on the two changed modules: 29 errors in 17 files, none in `judgment_model.py` or `model_router.py`.
- `lint-imports`: 3 kept, 0 broken. `tests/contract`: 95 passed. `check_versions.py`: OK.
- Existing GH-02 (`test_judgment_rules_floor.py`), `test_tier_routing.py` and `tests/test_decision` pass unmodified; the backend reason strings the GH-02 tests assert are unchanged.

VERIFIED (round 1, worktree on 275fe13):
- Full suite: 2527 passed, 13 skipped, 4 xfailed (baseline 2361 passed; +166 new). rc=0 checked directly.
- `ruff check` on the three changed files: clean.
- `mypy`: 41 errors in 26 files, all pre-existing; none in `judgment_model.py`, `model_router.py` or the new test.
- `lint-imports`: 3 kept, 0 broken. `tests/contract`: 95 passed.
- Existing GH-02 (`test_judgment_rules_floor.py`), `test_tier_routing.py` and `tests/test_decision` (151 tests) pass unmodified.
- Fail on base, run A (base `judgment_model.py`, new `model_router.py`): 85 failed, 81 passed. Failing: `test_dag_loan_review_in_t1_node_is_refused_not_served_at_t1`, `test_dag_vip_floor_above_cap_is_refused`, `test_dag_legal_floor_with_t0_backend_in_t1_node_is_refused`, `test_dag_conference_space_in_uncapped_node_lands_at_t1_not_t2`, `test_estimate_node_cost_for_refused_node_is_zero`, `test_every_legal_term_still_matches_as_a_whole_word` (9/9), `test_legal_prompt_is_floored_to_t2` (15/15, the `floor` field is absent on base), `test_non_legal_prompt_in_uncapped_node_is_t1` (26/30, the four that pass on base contain no substring hit), `test_non_legal_prompt_is_not_floored` (30/30).
- Fail on base, run B (base `model_router.py`): the module does not collect (`ImportError: cannot import name 'FloorRefusalAdapter'`), so every test fails; the probe script above shows the served-at-T1 behaviour directly.

ASSUMED:
- The VIP floor is still rules-only: `apply_rules_floor` never raised a backend choice for a VIP request (GH-02 design, "mirrors the first three branches"), so `safety_floor_for` does not name it either and a kev-served VIP request is clamped, not refused. Changing that is a routing-policy decision for the kev path, not a residual of this ticket; noted so nobody reads the backend tests as covering VIP.
- The corpus is 30 hand-written prompts; it bounds nothing about the real false-positive rate (no metric claim, n < 300).
- Plural handling (`s?`) is a judgment call; `spas` (the bathhouses) will floor. No production sample to weigh it.
- Callers of `router.route()` other than `invoke_routed_completion` and `_price_one_call` (none found in `CortexOS/` or `packs/`) would see a `RoutedModelCall` whose adapter refuses rather than an immediate exception.
