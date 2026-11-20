# JOIN-GRANT-SUBQUERY-01 (#408): the join grant only saw adjacent named tables (2026-11-20)

Gate: `CortexOS/insights/schema_context.py::ungranted_reason`, called by the
`/v1/insights` generate leg (`crew/cot_climb.py`). It is not the manifest
enforcer; `execution/manifest.py` was not touched.

## Expected vs actual (main `304bf84`)

- Expected: SQL that relates two declared tables with no JOINS line is a named
  REFUSE with no `sql_used`.
- Actual: 31 of 47 hostile statements left as `sql_used` (DMS executes it).
  The rest were refused by the SQL gate for other reasons (LATERAL, top-level
  set operation, table function).

## Repro

`python -m pytest tests/dms/test_join_grant_subquery_408.py -q` on main code
with the branch's test and corpus files: 122 failed, 65 passed. Shortest case,
schema with `alpha_ledger` and `beta_entry` and no JOINS section:
`SELECT qty FROM alpha_ledger WHERE id IN (SELECT ref_code FROM beta_entry WHERE amt > 0)`.

## Root-cause class

A gate that enumerates the spellings of a relation (FROM / JOIN operands that
are named tables) instead of deciding over everything the statement reads.
Four instances, three of them found only by the adversary round:

1. Operands that are not named tables (CTE, derived table, subquery in any
   clause, set operation leg, LATERAL) were never paired.
2. `_statements` read an empty statement (`;;`) as a parse failure, which
   returns None and switches every AST check off for SQL that still runs.
3. Pairs compared by bare name: a JOINS line for `sales.orders` granted
   `ops.orders` too.
4. A CTE named like a declared table: exempted, it hides the real table read
   from another scope; counted as the table, it is a bridge the statement
   never reads. No scope is resolved here, so that statement is refused.

## Rule now

All real tables the SQL reads (any clause, any depth, any statement of the
string) must be connected by granted JOINS pairs, compared as the declared
table each reference reaches. What cannot be named is
`schema_context:unanalysable_join`. The adjacent-pair rule is unchanged.

## Invariants that applied

- Assert the artifact the consumer receives: every corpus case is replayed
  through `POST /v1/insights`, not only against the function.
- Adversary is not the verifier: the writer's own 47 cases passed; four
  attackers on another model found three served bypasses in about 10 minutes.
- Prove a gate can fail: 12 mutations, 12 killed; `lint-imports` went red
  once during the build (a first version imported `CortexOS.execution`).
- Silent fallback is a lie: "parse failed, let the next gate name it" was the
  fallback that hid instance 2.

## Open, by name

- JOIN-GRANT-KEY-LEVEL: the grant is pair-level. ON predicates are not checked
  against the JOINS keys, and a bridge table the statement reads connects its
  neighbours (`a JOIN b ON true JOIN c ON a.k = c.k` passes when a-b and b-c
  are granted). Same on main for plain joins. Needs a founder ruling.
- TABLE-PRECHECK-CTE-BLIND: a CTE named like an undeclared real table hides
  that table from this function's table pre-check. `validate_caller_sql`
  refuses it (scope-resolved). Safety there rests on the second gate.
- SQL the parser rejects (`$$beta_entry$$`, zero-width characters) returns
  None here by design and is refused by `validate_caller_sql`. The unicode
  whitespace sweep was not finished.

## Subagent shape

5 attackers in parallel, one lens each (nesting and aliases; set operations
and LATERAL; DuckDB forms; identifier quoting, case and qualification; CTE
rule and parser disagreement), isolated context, read-only, one shared probe
harness that was first shown to flag the bypass on main code. Attackers on
Sonnet, build on the session model. Lenses 3 and 5 stopped early.
