# KEV-SHADOW: kev evaluated beside the rules, agreement logged, never serves (#248)

- **Date:** 2026-09-23
- **Keywords:** kev, shadow mode, judgment model, tier routing, agreement log, JSONL, degraded, order sensitivity, fail closed, CORTEX_KEV_SHADOW
- **Main idea:** `CORTEX_KEV_SHADOW=1` with `CORTEX_KEV_URL` keeps rules-v0 serving every tier decision (GH-02 floors included) and evaluates kev beside it via `decide(check_order=True)`, appending one row per decision to `data_path("engine", "tier_shadow.jsonl")`. The served `JudgmentDecision` is byte-identical to `rules_decide` whatever kev answers, times out, degrades or raises. `summary()` gives n, agreement rate and the degraded count, and flags `sufficient=False` below n=300.
- **Verify:** `python -m pytest tests/test_decision/test_shadow.py -q -p no:cacheprovider` (22 tests), then the full suite.
- **Does not prove:** any live kev server; any agreement number (the shadow log has n=0 rows in this repo); that shadow should ever be switched to serve (a founder decision gated on the KEV-CALIB report).

Epic #245 EPIC-KEV-LOOP. Base ca1d573. Worktree lane wf_ea572173-de3-4.

## Expected vs actual

Expected (issue #248): an operator can run kev beside the rules without kev
serving, and read back how often the two agree.

Actual on ca1d573: `JudgmentModel.from_env()` (CortexOS/routing/judgment_model.py)
installs `KevHttpBackend` as the serving `decision_backend` the moment
`CORTEX_KEV_URL` is set, and both production routers build the model that way
(CortexOS/db/lifespan.py:57, CortexOS/api/dag_run.py:75 via `ModelRouter()`).
There was no observation-only path and no agreement artifact.

## What was built

- `CortexOS/decision/shadow.py` (new): `ShadowEvaluator(backend, abstain_threshold, path)`
  with `observe(state, rules_tier)` that runs `decide(tier_question(), state, backend,
  check_order=True)`, builds the row `{schema_version, ts, backend, state_hash,
  request_type, rules_tier, kev_choice, kev_probs, kev_confidence, abstain,
  abstain_reason, degraded, cause, order_sensitive, calibrated, agree}` and appends it.
  `state_hash` is reused from `decision_log` (KEV-LOG, not touched). A backend that
  raises is a degraded row with `cause="raised <ExcName>"`; the exception text is not
  written. `cause` and `abstain_reason` are reduced by `cause_category()` /
  `abstain_reason_category()` to a fixed vocabulary (`parse`, `invalid json`,
  `transport: <ExcName>`, `http NNN`, `raised <ExcName>`, `unknown`, `other`;
  `degraded: <that>`, `order_sensitive`, `confidence X < threshold Y`) before the row
  is built, so no `str(exc)` ever reaches the file (verifier finding, below).
  `append_row` refuses rows carrying `content`, `prompt`, `system` or `state`
  and counts write failures (`write_failures()`, `last_write_error()`) instead of
  raising. `read_rows()` and `summary(path, min_n=300)` read the artifact back.
  Env: `CORTEX_KEV_SHADOW` (truthy = 1/true/yes/on), `CORTEX_KEV_SHADOW_PATH` override.
- `CortexOS/routing/judgment_model.py`: new `shadow` constructor kwarg; `from_env()`
  returns `cls(shadow=ShadowEvaluator(KevHttpBackend(url)))` when shadow is enabled and
  a URL is set, otherwise exactly the previous serving/rules behaviour; `decide()` on
  the rules-only path calls `shadow.observe` inside a bare `try/except` and returns
  the rules decision object unchanged. A serving backend plus a shadow leaves the
  shadow silent (no doubled kev calls).
- `tests/test_decision/test_shadow.py`: 22 tests.

## Repro

```
CORTEX_KEV_URL=http://127.0.0.1:8787 CORTEX_KEV_SHADOW=1 \
  python -c "from CortexOS.routing import JudgmentModel, JudgmentRequest; jm = JudgmentModel.from_env(); \
  print(jm.decision_backend, jm.shadow); print(jm.decide(JudgmentRequest('chat','hello')))"
# None <ShadowEvaluator>  then  JudgmentDecision(tier=T1, confidence=0.7, reason='heuristic routing fallback')
cat data/engine/tier_shadow.jsonl
# with no kev listening: one row, degraded=true, cause="transport: ConnectError", agree=false
python -c "from CortexOS.decision.shadow import summary; print(summary())"
```

## Root-cause class

Missing capability at a switch point: the only way to consult kev was to let it
serve. Class: "evaluation coupled to serving". No invariant was broken on base.

## Which invariant applies

- Served decision invariance: with a shadow present, `decide()` returns the very
  object `rules_decide()` built; the tests compare `(tier, confidence, reason)` against
  a separate `JudgmentModel().rules_decide(req)` for a seven-request set covering the
  T0 pin, birthday floor, legal floor, context, prior failures and VIP branches, and
  assert `"kev" not in served.reason`.
- Never raises: kev timeout, connect error, HTTP 500, unparsable body, a backend that
  raises `RuntimeError`, and an unwritable log path all leave the served decision
  identical; failures are counted, not raised.
- No raw prompt text in the log: a planted secret is put in `content`; the test greps
  the file for the secret and for `content`. Rows carry `state_hash` only. Two further
  tests point a respx kev at the same secret and make it echo `state.content` back in
  the response body (as a probability value, and as a non-JSON body); both grep the
  file for the secret and assert the row is `degraded=True` with `cause="parse"` /
  `"invalid json"`.
- No metric below n=300: `summary()` reports `sufficient=False` under `min_n`; the
  agreement rate is still computed so an operator can watch it climb, but it is
  flagged, and this finding claims no number.
- C2 boundary: `CortexOS/decision/shadow.py` imports stdlib, `CortexOS.paths` and
  `CortexOS.decision.decision_log` only. `lint-imports` 3 kept, 0 broken; `tests/contract` 95 passed.
- Loopback only: shadow constructs `KevHttpBackend(url)`, so a non-loopback
  `CORTEX_KEV_URL` still raises at `from_env()` (tested).
- Do-not-touch list respected: no change to `decision_log.py`, `labels.py`,
  `scripts/**`, `CortexOS/execution/**`, `tests/contract/**`, `tests/invariants/**`,
  `.importlinter`, `contract/**`.

## Design decisions worth knowing

- Shadow hangs off the rules-only branch of `decide()`, not off `RulesBackend`, so the
  KEV-LOG `describe_decision` still sees `decision_backend is None` and keeps
  recording rules-v0 confidence in `tier_decisions.jsonl`. The two logs join on
  `state_hash` (same function, same state dict).
- `agree` is true only when kev produced a choice equal to the served tier. A
  degraded or abstaining kev counts as disagreement in the rate, because it did not
  make the decision; `summary()` also reports `degraded`, `abstained` and
  `order_sensitive` separately so the rate can be read against them.
- `check_order=True` means two kev calls per decision (the PRD's "shadow doubles kev
  calls" risk); the test asserts exactly `2 * len(REQUESTS)` calls.

## Verifier findings (round 1, addressed)

**Blocking: raw prompt text could reach `tier_shadow.jsonl` via `cause` and
`abstain_reason`.** `KevHttpBackend.evaluate` returns `RawDecision.failure(name,
f"parse: {exc}")` (backends.py:148) and `f"invalid json: {exc}"` (:144); `str(exc)`
for `float("<text>")` quotes the value, and the value is whatever kev put in its
response body. `build_row` copied `answer.cause` and `answer.abstain_reason` into the
row verbatim, and `_FORBIDDEN_KEYS` checks key names only. Reproduced with the
verifier's probe (`/tmp/kevprobe/probe.py`, a respx kev that echoes
`state.content` into a probability): `LEAK in shadow file: True`, row `cause` =
`"parse: could not convert string to float: 'PLANTED-SECRET-XYZ hello'"`. The served
decision was still the rules' (T1, `heuristic routing fallback`); only the log leaked.
KEV-LOG's `decision_log.py` does not write `cause`, so 93a4275 introduced it.

Fix (`CortexOS/decision/shadow.py`): `cause_category()` reduces any cause to a fixed
vocabulary and `abstain_reason_category()` does the same for the reason, both applied
in `_row`, the one point where backend text meets the file. Only an exception *class
name* survives (`transport: ReadTimeout`, `raised RuntimeError`), and only when the
detail is a bare identifier; `parse: ...` and `invalid json: ...` lose their detail
entirely; `http NNN` and `unknown` pass; anything unrecognised is `other`. The
existing `cause_fragment` assertions (`transport: ReadTimeout`, `http 500`, `parse`)
and `cause == "raised RuntimeError"` are unchanged and still pass.

Tests added (`tests/test_decision/test_shadow.py`):
`test_shadow_kev_echoing_prompt_into_body_never_reaches_file` (probability echo),
`test_shadow_invalid_json_body_echoing_prompt_never_reaches_file` (raw body echo),
and parametrised unit tests over both categorisers. Before the fix (shadow.py stashed
back to 93a4275) the two echo tests fail on `assert SECRET not in text` with the
secret visible in `cause`; after it they pass and the probe prints
`LEAK in shadow file: False`.

## Verified vs assumed

VERIFIED:
- Round 2: `tests/test_decision/` 78 passed (22 + 20 new shadow tests + the rest);
  ruff clean on `CortexOS`, `tests/contract`, `tests/test_decision`; `mypy
  CortexOS/decision/shadow.py` clean; `lint-imports` 3 kept, 0 broken;
  `tests/contract` 95 passed; full suite 2328 passed, 13 skipped, 4 xfailed, exit 0
  (exit code read from the run's own output file, not through a pipe).
- New tests: 22 pass on the change. With `CortexOS/routing/judgment_model.py`
  copied aside and replaced by `git show ca1d573:CortexOS/routing/judgment_model.py`
  (then restored), 17 of 22 fail: the disagreement/agreement rows, the router path,
  all four kev-failure cases, the raising backend, order sensitivity, the unwritable
  path, shadow-unset serving, the four falsy-value cases, shadow without URL, and
  shadow beside a serving backend. The 5 that pass on base are pure tests of the new
  `shadow.py` module (`summary` x2, `append_row` refusal, default path) plus the
  non-loopback refusal that base `KevHttpBackend` already raises. On a true ca1d573
  checkout the file fails at import because `shadow.py` does not exist.
- Existing KEV-DECIDE, GH-02, KEV-LOG and tier-routing tests are unmodified
  (`git diff --stat ca1d573 -- tests/` is empty apart from the new file) and pass: 70 passed.
- ruff on the three changed files: clean.
- mypy: 41 errors in 26 files, identical to the stated baseline, none in the changed files.
- lint-imports: 3 kept, 0 broken.
- tests/contract: 95 passed.
- Full suite: 2308 passed, 13 skipped, 4 xfailed, exit 0 (exit code read from the
  run's own output file, not through a pipe). Baseline on ca1d573 is 2286 passed; the
  difference is the 22 new tests.

ASSUMED:
- The production routers pick shadow up unchanged because they call `ModelRouter()`
  with no judgment model, which calls `JudgmentModel.from_env()`; verified by reading
  `model_router.py:53`, not by booting the API.
- Concurrent appenders from several processes are safe because each row is one
  `write()` in append mode under a thread lock; not proven with a contention test.

## Not done

- `request_type` is written verbatim, as `decision_log.py` (KEV-LOG) already does
  (decision_log.py:143). It is an engine-set routing category (`chat`,
  `intent_classify`), not prompt text; a caller that puts free text in it would see
  it in both logs. The verifier probe exercises this and it is unchanged here because
  it is not a crossing this commit introduced and the two logs must keep matching
  on it. Bounding it to a known set would be a joint KEV-LOG/KEV-SHADOW change.
- No agreement number: the shadow log has n=0 rows here.
- No serve cutover, no threshold tuning, no calibration join over the shadow rows
  (KEV-CALIB reads the decision log; joining shadow rows by `state_hash` is a follow-up).
