# ROUTER-2 ladder (#270) and ROUTER-3 pre-spend gate (#271)

Branch `claude/cortex-r3-router` from `claude/cortex-scale-agi-state-fkg2b6` @ fa0f8a8.

## Expected vs actual

| | Expected (issue) | Actual on base | Now |
|---|---|---|---|
| #270 | A rejected answer steps up to the next-cheapest model; every rung stamped; no rung passing = honest refusal | `complete()` sends one request. A checker reject returns `ok=False` and the rejected text; the next *call* may pick another model, never this one | Opt-in `ladder=N` / `solvers=` / `CORTEX_FREEROUTE_LADDER=N`. Rungs: solvers (deterministic; classic ML disabled without held-out numbers), then models by measured mean token cost. Step-up on a checker reject or a non-refusal failure; stop on any `_STATUS_REASONS` status. `stamp.ladder[]` gets copied onto the Insights envelope. No rung passing gives an empty-text refusal |
| #271 | Ask KEV-DECIDE before a paid call; skip on abstain/no; degraded falls back to calling visibly | `decide()` only feeds the execution `ModelRouter` tiers; nothing in front of `freeroute.complete()` | Opt-in `CORTEX_FREEROUTE_PRESPEND=shadow|enforce`. `enforce` without a tuned threshold, or an unknown mode, refuses and spends nothing. A degraded backend spends and says so on the stamp. Predictions go into the route store; `tune()` reads train rows only and `report()` reads held-out rows only |

## Repro

```
python -m pytest tests/test_freeroute_ladder.py tests/test_freeroute_prespend.py -q -p no:cacheprovider
```
Both files fail to collect on fa0f8a8 (the ladder and gate APIs are absent). `test_sql_lane_passes_its_plan_to_the_gate` also fails when only `sql_generator.py` is reverted.

Live (evidence only, n=1, env-direct, split=benchmark): `ladder=1` with a checker that rejects the first answer. gemini-3-flash-preview served and was rejected, then kimi-k3 served, was accepted and is marked `final`. At `max_tokens=20` kimi came back empty (reasoning budget) and the ladder ended as an honest refusal with empty text.

## Root-cause class

The single-shot model call had no in-call recovery tied to the checker. The spend decision had no predictor in front of it. Both are *missing control points*, not bugs.

## Invariants that apply

- Silent fallback is a lie. Every step-up, left-out candidate, ignored env value and degraded gate is named on the stamp.
- A control that blocks legitimate work is a failure. Both features are opt-in and byte-for-byte unchanged when unset. `tune()` only gives a threshold that keeps every good train row.
- A skipped test is a failing test. Neither new file skips.

## Verified vs assumed

- **Verified:** the stubbed tests (34), the full suite, ruff, lint-imports and check_versions on this branch, and one live env-direct step-up.
- **Not measured:** held-out coverage, WRONG, cost and p95 against the ROUTER-1 baseline. The same goes for #271's ECE, Brier and automatable share: there is no trained kev model or dev set, and there are no shadow rows yet.
- **Unmet dependency:** #268 masking is not on the base (`MASKING_STATE="off"`), so "every rung goes through #268's mask" cannot hold yet.
- **Not done (founder decision):** the #270 guard restore, which would put `CortexOS/integrations/freeroute.py` back on the no-touch lists in `test_cot_climb.py` and `test_prompt_harness_climb.py`. Those guards diff against `origin/main`, where this whole branch line already changes `freeroute.py`, so adding it now fails at once. The handoff says not to edit the guards.
- **Assumed:** stepping up on a non-refusal failure (500, 504) is within "step up on reject", since those statuses are already scored against the model.
- **#225:** it is a proxy V(s,a,g), not a trained predictor. Nothing from it is reused.
