# EPIC-KEV-LOOP: tier decisions that learn from outcomes, without hand labels

Base: claude/cortex-scale-agi-state-fkg2b6 @ d4ffd70. Evidence from the read-only kev-loop discovery lane (workflow wf_3a6328d2-5f2), file:line cited, VERIFIED unless marked ASSUMED.

## Problem

KEV-DECIDE (PR #235) gave Cortex a calibrated decision port, but its 0.5 abstain threshold is a guess, and nothing can tune it:

1. **No decision log.** `ModelRouter.route()` (CortexOS/execution/model_router.py:56-77) returns a reason string only; the judgment confidence and backend probabilities never leave `route()`. `node_executions` (CortexOS/db/sql/node_executions.sql:4-20) has no reason, backend, or confidence columns.
2. **No outcome labels.** `prior_tier_failures` is read (judgment_model.py, model_router.py:62) but no code ever sets it. The real signals are node status plus eval/scoreboard predicates. Infra errors (cost ceiling, transport) are not evidence about the tier.
3. **No calibration report.** `fit_temperature`, `ece`, `brier` and `automatable_share` exist (CortexOS/decision/calibration.py) with no data join or CLI.
4. **No shadow mode.** Setting `CORTEX_KEV_URL` makes kev serve immediately (judgment_model.py `from_env`); the production routers are built that way (CortexOS/db/lifespan.py:57, CortexOS/api/dag_run.py:75).

## Buyer-visible outcome

An operator can run kev beside the rules without it serving, see how often the two agree, and get a calibration report that either states n, class balance, ECE, Brier and the automatable share at a chosen error budget, or refuses with `INSUFFICIENT` when there is not enough labelled data. The founder never hand-labels; the founder only picks the error budget.

## Non-goals

- No auto-tuning or serve cutover. Moving the threshold or letting kev serve is a founder decision gated on the report (n >= 300 labelled rows with >= 30 negatives).
- No new escalation loop, and no change to node_executions SQL.
- No accuracy claims below n = 300.

## Tickets

| Id | Scope | Write targets | Depends on |
|---|---|---|---|
| KEV-LOG | Append-only JSONL tier-decision log, one call site in `invoke_routed_completion` (ok, error and T0 paths), never raises, schema_version, `state_hash()` | `CortexOS/decision/decision_log.py`, `CortexOS/execution/executor.py`, `tests/test_decision/test_decision_log.py` | none |
| KEV-CALIB | `labels.py` joins the decision log with ledger status and scoreboard predicates into (probs, label) with an excluded-reason tally; `scripts/kev_calibration_report.py` prints n, exclusions, class balance, T, ECE, Brier, automatable share at 0.02/0.05/0.10; exits non-zero with `INSUFFICIENT` below n=300 | `CortexOS/decision/labels.py`, `scripts/kev_calibration_report.py`, `tests/test_decision/test_labels_and_report.py` | KEV-LOG |
| KEV-SHADOW | `CORTEX_KEV_SHADOW=1` keeps the rules serving and evaluates kev beside them, logging agreement; never raises, never changes the returned decision | `CortexOS/decision/shadow.py`, `CortexOS/routing/judgment_model.py`, `tests/test_decision/test_shadow.py` | KEV-LOG |

## Risks

- Label noise: with no scoreboard links, almost every label is "sufficient", and ECE is then uninformative. The report must print the class balance and refuse on too few negatives.
- The decision log could hold prompt content. It stores `state_hash` and request metadata, never raw prompt text (GH-01 redaction does not cover the log).
- Shadow doubles kev calls on the loopback; it runs only when explicitly enabled.

## Verified vs assumed

- VERIFIED: the gaps above with file:line.
- ASSUMED: fitting temperature on log(p) as pseudo-logits is acceptable for a probability-returning backend; the report states this.
