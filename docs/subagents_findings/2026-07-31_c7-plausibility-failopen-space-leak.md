---
keywords: C7, plausibility, route_to_metric, confidently_wrong, fail-open, compliance_gate, space ACL, cross-space leak, R-0011, R-0002, R-0004, A-0004, postgres, RLS
main_idea: >
  Three defect classes found by asserting the customer artifact instead of the
  intermediate: a keyword-cascade branch answering a different question than
  asked, nine copy-pasted fail-open gates on the only write loop, and a Space
  boundary that exists in tested code with no production caller.
---

# C7 plausibility, fail-open mutations, and the Space boundary that never ran

**Date:** 2026-07-31 · **Lane:** Claude Code, DMS-anchored NOW sequence
**Repos:** `D:\Cortex`, `D:\DMS` · **Stack:** :8010 :8090 :5000 live throughout

## What was asked

Anchored sequence Phase NOW: C7-prod schema gate (Prompt I), claim_n review
(Prompt J), then Postgres -> Amend -> Spaces.

## What actually mattered

### 1. A keyword branch that answered a different question (P0, live)

`total revenue for SKU-00397` returned `sku_count = 509` - the count of every
SKU in the warehouse - badged `L1_GOVERNED_METRIC` with a drillthrough token.

Root cause: `\bskus?\b` matches *inside* the identifier, because the hyphen is a
word boundary. The population-count branch therefore fired on a question about
one entity.

**The instructive part:** excluding that one branch did not fix it. The same
wrong answer moved to `revenue_total = 80,375,993.99`, the whole warehouse's
revenue reported as one SKU's, because the next branch guards on the *normalized*
text where the identifier no longer reads as "sku". Two branches, one defect;
patching branch-by-branch was never going to converge (R-0004).

Fix: one check on the way out of `route_to_metric` - name a SKU, and the plan's
slots must contain it. Exclusions pass free because they resolve the SKU into
`exclude_skus`, so it *is* in the slots.

**Generalisable:** when a regex fires on a *word* that also appears inside an
*identifier*, expect the same defect in every sibling branch. Fix at the funnel.

### 2. Nine fail-open gates on the only write loop (P0)

Every write route called `compliance_gate` then hand-rolled:

```python
if not decision.allowed and decision.reason not in {
    "gate_unavailable", "gate_task_unknown",
}:
    raise HTTPException(403, decision.reason)
```

Refuse when refused, *except* when no compliance decision was reached at all.
With Cortex down every mutation proceeded ungated, and the ledger append failed
in the same outage, so nothing recorded it. `cortex_client/gate.py:68` already
carried the comment "fail closed for mutations" - the routes were overriding
their own client, nine times, by copy-paste.

**Generalisable:** when a client encodes a posture and every caller re-derives
it, the callers will drift and the drift will all point the same way (permissive).
Grep the *exception set*, not the call site: `test_mutation_routes_call_
compliance_gate` passed the whole time, because it checks the gate is *called*,
not that its answer is honoured.

### 3. A Space boundary with no production caller (P0, open)

`intersect_space_grants` / `resolve_session_acl` are implemented, unit-tested and
green. `Executor.live_ask` calls `demo_acl()` instead - every table, predicate
TRUE, regardless of `space_id`. Confirmed live: "Margin sandbox" holds only
`inventory` + `locations`, and a `transactions` question inside it returned the
full revenue ranking, badge `L0_CERTIFIED`.

Not wired this session, on evidence: `DEMO_TABLES` needs 6 tables, `data_sources`
covers 3, `acl_grants` has 0 rows - so `list_user_source_grants` returns nothing
and enforcement would refuse 100% of asks (R-0005). Pinned as
`xfail(strict=True)` so the suite fails when it starts passing.

**Generalisable:** "tested and green" is not "reachable". Grep for a production
caller before believing a security control exists.

### 4. Honesty inversions found next to the flag being flipped

- `persisted` / `database_configured` were `bool(settings.database_url)` - a
  claim about *configuration*, not storage. DB down -> silent memory fallback ->
  API reports `persisted: true` for a Space that dies on restart (R-0011).
- `run_gate` set `explain_ok=True` when no connection was passed, which reads
  downstream as "EXPLAIN approved this SQL" (R-0011).
- `tests/control_plane` skipped its whole suite when Postgres was unreachable -
  which on a fresh machine is always - so the only proof that tenant isolation
  isolates never ran while the suite reported green (R-0002).

**Generalisable:** before flipping a `*_configured` flag, grep what reads it. The
honest-empty-plus-hint state is often *better* than the configured-but-empty state
that replaces it.

### 5. A-0004 again: stale process serving old code

Live ask was 503 for the whole session start. OpenVault `POST /keys/services`
500'd, while `/api/healthz` and `/keys/jwks` were 200. On-disk code against a
*copy* of the same key store returned 200 - so not code, not data. The running
process predated its own working tree. Restart fixed it.

**Method worth reusing:** reproduce against a *copy* of the real state before
suspecting the state. It separates code / data / process in one step and mutates
nothing. `keys.db` mtime also identified which of two candidate homes was live.

## Verify commands

```bash
# Cortex
PACK=dms DMS_READ_ONLY_QUERIES=1 python -m pytest tests/dms/test_c7_prod_gate.py -q
PACK=dms DMS_READ_ONLY_QUERIES=1 python -m bench.corpus          # 376 wrong=0
lint-imports                                                      # NOT python -m importlinter.cli
# DMS
python -m pytest tests/ -q --ignore=tests/control_plane
DATABASE_URL=postgresql://dms:dms@127.0.0.1:5432/dms python -m pytest tests/control_plane -q
docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.hostdb.yml up -d postgres
```

## Gotchas for the next agent

- Running the Cortex suite while :8010 is live fails 5 lakehouse tests on the
  DuckDB write lock. Correct behaviour (R-0002 - it fails, it does not skip).
- `lint-imports` is not on PATH here:
  `C:\Users\OoiJianHong\AppData\Roaming\Python\Python314\Scripts\lint-imports.exe`.
- `verify_gold --review` needs a TTY and a human by design. claim_n stays 47/310
  until a person reviews. Do not build a batch importer - the TTY *is* the gate.
- `git add tests/repos/` creates bare gitlinks for the cloned CRAG corpora. Ignored now.
- The Bash tool is Git Bash: PowerShell here-strings (`@'...'@`) fail. Use a heredoc.
