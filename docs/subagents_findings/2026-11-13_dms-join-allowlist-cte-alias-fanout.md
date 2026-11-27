# DMS: the join allowlist counts physical tables, so a CTE joined to itself serves a fanned-out total as validated

Date: 2026-11-13. Repo: DMS (`Netie-AI/dms`), main `ebe65cc`. Ticket: dms#527 JOIN-HOLE-01 (P0, #277 lane under #178, provisional ledger row F-TBD-39). No repository file was changed to find this: every run used scratch worktrees.

## Expected vs actual

Expected (DMS CLAUDE.md rule 16): SQL that reads two or more tables must pass `space_ontology.unverified_join_reason`, one allowlist of verified links, and "everything else abstains". A self-join is a join.

Actual: a statement that reads ONE physical table more than once through a CTE alias is allowed, executed, and served `L2_VALIDATED` with `abstained` false and a multiplied figure in the text, the rows and the values.

| Where | Statement shape | Figure shown | True figure |
|---|---|---|---|
| main, Space with a verified ontology, `POST /v1/chat/ask` | `WITH c AS (SELECT * FROM t) SELECT SUM(a.x) FROM c a, c b` | 428 | 107 |
| same | `... FROM c a CROSS JOIN c b` | 428 | 107 |
| same | `... FROM c a JOIN c b ON a.k = b.k` | 24 | 10 |
| main, contract ask (`sql_used`) | JOIN form / comma form | 24 / 428 | 10 / 107 |
| #522 branch, file Space with no ontology | JOIN form / comma and CROSS | 400 / 1000 | 250 |

Function level only (real function, empty `Ontology()`): the `USING` form, three references (`FROM c a, c b, c d`), a scalar subquery over the same CTE multiplied by `COUNT(*)`, and a recursive CTE crossed with a table are all allowed. The recursive form is stopped on the envelope by a different gate (`validate:ungranted`), not by the allowlist.

Controls that hold: a plain self-join (`FROM t a JOIN t b`) is refused ("has no declared link"), a derived-table self-join is refused (`derived_table_over_join`), a CTE joined to a second physical table is refused (`cte_over_join`).

All envelope runs used the test fixtures' fake Cortex. A real Cortex was not exercised; it might add a check the fake lacks.

## Repro

Function level, from the DMS repo root with the four packages on `PYTHONPATH`:

```python
from dms_executor.space_ontology import unverified_join_reason
from dms_executor.ontology import Ontology
cols = {"orders": {"order_id", "customer_id", "amount", "region"}}
sql = "WITH c AS (SELECT * FROM orders) SELECT SUM(a.amount) FROM c a JOIN c b ON a.customer_id = b.customer_id"
print(unverified_join_reason(sql, Ontology(), [], columns_of=cols))   # None, i.e. allowed
```

On three rows whose true `SUM(amount)` is 220 the statement returns 370; the comma form returns 660.

Envelope, worst case on main: a test that imports `tests/test_onto_derive_01.py` as `od` with its `minter` and `store` fixtures, then

```python
rig = od._space(tmp_path, monkeypatch, minter)
rig.ask("What is the total?", {"query_sql": "WITH c AS (SELECT * FROM bronze.public_enrollments) SELECT SUM(CAST(a.students AS INTEGER)) AS total FROM c a, c b", "plan_source": "ontology_plan"})
```

returns `L2_VALIDATED`, "Found 1 row(s). total=428". The true total is 107.

## Cause

`_JoinRule.check` (`packages/executor/dms_executor/space_ontology.py:1018-1031`) builds `tables` from scope sources that are `exp.Table`. A CTE alias in a FROM clause is a Scope source, not a Table, so one physical table referenced through an alias any number of times counts once. `len(tables) <= 1` is then true and the method returns before the set-operation, CTE and link checks run. Nothing after it (EXPLAIN, grain checks, the fake Cortex) looks at fan-out.

## Root-cause class

**A safety rule that counts the wrong unit.** The rule's subject is "relations joined in a FROM clause"; the code counts "distinct physical tables in the parse tree". The two agree for every shape the original tests used and diverge as soon as an alias layer (CTE, recursive reference, anything that resolves to a relation without being a table node) sits between them. The early return on the proxy count skips every later check, so the gap is total, not partial.

Sibling to look for elsewhere: any gate that says "if the cheap count says there is nothing to check, return" before the expensive check.

## How it was found

Not by an attack run. The Epic Agent was writing a ticket to ALLOW a same-table CTE (dms#525, a coverage request raised by the adversary on #522) and, to state the current behaviour honestly, replayed the counting rule in sqlglot outside the repo. The probe showed the alias case passing. The orchestrator then called the real function, and a separate agent reproduced it on the envelope with a plain self-join as the control. A request to widen a rule was the thing that exposed that the rule was already too wide.

Also relevant: #522's own new test file listed "cte_referenced_twice_self_join" under shapes that "still answer", asserting only that an answer came back. A test that asserts "it answers" without the figure pins a wrong number as correct behaviour.

## Invariants that apply

- Assert the artifact the customer receives: the function-level result alone would have been arguable; the envelope with 428 against 107 is not.
- Prove a gate can fail before trusting it: the allowlist's tests never included a shape where the unit it counts and the unit it protects differ.
- Fix the root-cause class, not the symptom: the ticket requires counting every FROM/JOIN source that resolves to a relation inside the one allowlist, not a list of refused CTE spellings.
- A control that blocks legitimate work is itself a failure: the fix must list every one-table CTE that newly abstains (a CTE referenced once must keep answering).
- Do not claim "0 wrong" below n=300: the earlier live run (n=7) and the #522 adversary round (about 30 shapes) both reported no wrong figure; this shape was in neither.

## Other things the same day's rounds found (each has an owner)

- Held-SQL Yes runs SQL that was parked before the join gate, locally, with no Cortex submit and no ledger append; reproduced with both flags on in a Space with an ontology (dms#524, unreachable at default flags).
- With `DMS_ONTO_FROM_DATA=1`, approved ontology items are install-wide (dms#523; one founder question on items that name no Space).
- A steward-registered join is served `L0_CERTIFIED` in a file Space with no ontology (founder question recorded on dms#178); a model-saved verified query may replay as certified (dms#526, read from code, parked).
- After #522 the allowlist's reference counting also refuses some one-table statements in file Spaces ("above average" is being fixed inside #522; a same-table CTE is dms#525, now held behind #527).
- A "no cell value leaks" canary test missed the top values of its own fixture and every decimal-less form; fixed in dms#521 with a test that checks the patterns against every generated cell.

## Agent shape

Orchestrator on the session model. Fan-out on Sonnet, each with isolated context and a written brief:
PRD Agent (routing, resumed for each new report), Epic Agent (tickets, resumed), three ticket runners as separate cloud sessions (one writer per new branch: #514, #520, DuckDB pin), one adversary agent per draft PR in scratch worktrees (report only, mutations restored), one reproduction agent for the envelope check. The orchestrator re-ran the decisive checks itself (function results, two mutations, one deliberate leak) rather than relaying them.
