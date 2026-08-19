---
date: 2026-08-20
topic: verify-reopened-six-of-seven
keywords: [R-0003, independent-verify, adversarial-verify, false-green, self-issued-grant, manifest, exclusion-span, stop-list, eval-gate, REVERT-A, readme-claims, negation-blindness, session-limit, partial-agent-work]
main_idea: >
  Adversarially prompted independent verification reopened six of seven tickets a
  previous run had recorded as shipped. Every claimed gap was reproduced by a
  second adjudicator, so none were verifier error. Four defect shapes recur and
  are worth checking for by name in any repo.
---

# Independent verify reopened six of seven "shipped" tickets

## What was done

Seven tickets recorded as shipped-and-awaiting-verify were given one verifier
each, prompted to **refute** rather than confirm, and told to default to
FAIL/PARTIAL when uncertain. Every claimed failure then went to a second,
independent adjudicator whose job was to decide whether the gap was real or a
verifier artifact - because a false FAIL blocks legitimate closure and is its own
failure (R-0005).

Result: **1 PASS, 5 PARTIAL, 1 FAIL.** All six refutations held under
adjudication. The prior run had recorded two of these as closed.

## The four recurring shapes

**1. A gate that intersects against a set it also mints.** The manifest-grounding
gate compared the SQL's tables against the session grant. When no session bound a
manifest, the engine minted a self-issued grant containing *every table the pack
declares* - so the intersection could never be empty on the path customers use.
The gate was not weak, it was **vacuous**, and it read green forever.
Generalisation: when a check compares A against B, ask who produces B. If the
same component produces both, the check is an identity.

**2. A fix that ends a clause negatively.** Exclusion parsing decided where an
entity clause ended by popping words on a stop-list. The documented root-cause
class was "two lists can disagree", and the fix made the lists agree *today* -
so any word neither list knew (`also`, `just`, `kindly`) reopened it. Ending the
clause **positively**, at what resolves, removes the ability to disagree.
Generalisation: a fix that enumerates cannot close a defect whose class is
"the enumeration is incomplete".

**3. A deliberate defect left in the tree.** `_score_live` carried three
`# REVERT-A` lines short-circuiting its own fix to the code path it replaced -
the reintroduced defect from an R-0007 proof, never restored. The suite was green
because the tests for it were written but the fix behind them was disabled.
Generalisation: grep for revert/neutralise markers before trusting a proof
someone else recorded; the proof and the restore are two steps and only one of
them is exciting.

**4. A gate blind to the sentence it exists to catch.** A README claim gate
dropped any sentence containing a negation, to allow honest disclaimers. The one
unbacked claim on the front page denied one control while asserting another in
the same breath - "X is **not** shipped, code runs in Y" - so the gate could not
see it. Widening at dashes and semicolons still passed; only splitting at
**commas** made it fire. Generalisation: an exemption rule needs a test that the
exemption cannot swallow the thing being checked.

## Also learned

- **A "fix" can replace one unbacked claim with another.** The Wasm sandboxing
  claim was correctly deleted and replaced by "untrusted code is run in
  containers" - equally false, since the runner refuses the docker stack
  outright. Verify replacement text as adversarially as the text it replaced.
- **Partial agent work survives an agent dying.** Nine agents hit a session
  limit and all reported failure, but had already written 221 insertions, two
  test files, and 26 deletions. Treat a failed run as *possibly having mutated
  the tree*: check syntax, conflict markers, and whether deletions match intent
  before assuming nothing happened.
- **A corpus can cover half a commit.** `ebd049b` fixed a follow-up path and a
  single-question path. Reintroducing the follow-up half turned the gate red;
  the single-question half left it green, because every seed in the relevant
  category replays turns. Coverage is per *shape*, not per commit.
- **`pytest --timeout=N` exits 0 having run nothing** when `pytest-timeout` is
  absent - it errors on the argument and still returns 0. Same class as
  `python -m importlinter.cli` on Windows (R-0007).

## Cross-refs

`2026-08-01_estate-audit-false-greens-and-identity.md` (the original three false
greens), `2026-07-31_c7-plausibility-failopen-space-leak.md` (assert the customer
artifact). Tickets: Cortex#5, #6, #7, #8, #9, #10, #14; filed #36, #37.
