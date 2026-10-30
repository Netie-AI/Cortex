# Metric reconfirm: placeholder closest question, no ticket on No, blank legacy miss

Date: 2026-10-30. Branch `claude/cortex-reconfirm-quality`. Follows #424.

## Expected vs actual

| Gap | Expected (CLAUDE.md 8/9) | Actual on main `aae3cbc` |
|---|---|---|
| Closest question | The closest *answerable* question; Yes runs it | Chooser `none` with no suggestion rendered "Closest question: a declared question" (36 of 51 reconfirms in the armed run). Yes on it reconfirmed again. Other suggestions were fragments or showed metric ids. |
| No on the metric reconfirm | "not found in the database" and a pipeline-failure ticket | Not found, no ticket. Only the plan+SQL path logged one. |
| Legacy bridge document miss | Falls through to the ladder | `{answer: '', route: 'rag', sources: []}`, no badge |

## Repro

`tests/dms/test_metric_reconfirm_quality.py`. Each guard was removed in turn
and its test went red (9 of 9).

## Root-cause class

A model's output was trusted as customer text without being checked against
what it claims. The closest question claims "Yes runs this"; nothing asked
it. Fix: the engine asks the suggestion back through the same choose and
check; only a served one is offered, else an honest fixed text, and Yes on
that text answers not found with a ticket.

## Invariant

"Assert the artifact the customer actually receives": the #424 gates checked
that a reconfirm happened, not that its closest question was runnable.
"Silent fallback is a lie": the placeholder looked like a suggestion.

## Not verified

No armed run on this branch. The round-trip costs two extra model calls per
reconfirm; the armed rate of real suggestions is unmeasured.
