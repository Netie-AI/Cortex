# DMS answer router — complete state map

**Established:** 2026-07-27 by live probe against the running engine (`:8000`) and
by reading `CortexOS/dms/query_service.route_question` +
`CortexOS/dms/answer_engine.answer`. Every row below was **observed**, not
inferred from the code.

There are two routers in series. Confusing them is the usual source of "why did
it say that":

```
question
   │
   ├─ ROUTER 1  route_question()          coarse: is this even a data question?
   │      blocked ──────────────────────────────────────────────► terminal
   │      rag ─────────────────────────────────────────────────► terminal
   │      sql / needs_clarification ─┐
   │                                 ▼
   └─ ROUTER 2  answer()             fine: which trusted asset answers it?
          session → certified(L0) → governed_metric(L1) → query_skill → abstain(L3)
```

Router 2 is strictly ordered and **first-match-wins**. It never consults a later
layer once an earlier one produces SQL.

---

## 1. Router 1 — coarse routing (`route_question`)

| State | Fires when | Response shape | Live probe |
|---|---|---|---|
| `blocked` | `destructive_intent()` returns a reason | `violations_blocked:["DDL_ATTEMPT"]`, no rows, `layer:"blocked"`, conf `0.85` | `"delete all inventory rows"`, `"wipe all supplier records"` |
| `rag` | question **names a document** (contract / SOP / agreement / policy / terms) | `route:"rag"`, `sources:[…]`, `layer:"rag"`, conf `0.85` | `"what does the SOP document say about cold chain?"` |
| `sql` | a warehouse keyword matches → hand to Router 2 | — | `"Show expired items"` |
| `needs_clarification` | nothing matched | falls through to Router 2, which abstains | `"what is the airspeed velocity of an unladen swallow"` |

Both Router-1 classifiers were rebuilt on 2026-07-27; see
`tests/dms/test_destructive_intent.py` for the before/after contract.

- `destructive_intent` was `\b(drop|delete|truncate|alter|insert|update|create)\b`
  over raw English. It **refused** `"update me on the delayed shipments"` and
  `"cost by drop-off point"`, and **missed** `"wipe all supplier records"`.
  It is now SQL-statement shapes + (mutation verb → data object) with benign
  idioms stripped first, and it returns an auditable cause
  (`sql_write_statement:drop table`, `mutation_intent:wipe all`).
- `RAG_KEYWORDS` fired on the bare openers `what does` / `explain`, so
  `"what does shipping cost us by destination"` was answered out of the supplier
  contract corpus — a confident zero-row answer. RAG now requires a document noun.

**Enforcement is not here.** `sql_guardrail` parses every statement with sqlglot
and rejects `Insert/Update/Delete/Drop/Create/Alter/Truncate` regardless of
wording. Router 1 exists to refuse *intent* early and record it.

---

## 2. Router 2 — trusted-asset routing (`answer_engine.answer`)

Ordered. Each state's `layer` and `metric_id` are reported honestly in
`query_plan`.

| # | State | Fires when | Confidence | Live probe |
|---|---|---|---|---|
| 1 | `session` | prior turn in this `session_id` **and** anaphora (`them`/`those`/`average of them`) | `0.88` | `"Top 5 selling SKUs by revenue"` → `"average of them"` = `avg_sales_value_myr 590996.79` |
| 2 | `certified` (L0) | **exact** normalized match in `certified_queries.yaml` | `0.95` | `"Top 5 selling SKUs by revenue"` → `cq_sales_top5_value` |
| 3 | `governed_metric` (L1) | `route_to_metric()` matches a rule → compile `metrics.yaml` template | `0.95` | `"last month sales"` → `revenue_last_month` |
| 4 | `query_skill` | similarity ≥ 0.72 against a previously answered question | `max(0.72, score)` | **never observed in production traffic — see §4** |
| 5 | L2 freeform | `DMS_L2_ENABLED` set **and** a model wired | — | **unreachable: no model is wired; the flag only changes the abstain reason** |
| 6 | `abstain` (L3) | nothing above produced SQL | none | `"how profitable was the Berlin office in 1997"` |

### Sub-states of `abstain`
Same route (`needs_clarification`), different `reason` — worth separating because
they mean different things operationally:

| Reason | Meaning | Action |
|---|---|---|
| `no governed metric or certified query matched` | coverage gap | add a metric or a certified query |
| `could not resolve inputs: …` | a metric matched but a slot failed validation (bad location code, out-of-range int) | fix the value dictionary |
| `internal SQL failed guardrail […]` | a **governed template produced SQL the guardrail rejected** — always a bug in `metrics.yaml`, never user error | fix the template |
| `no verified answer path (L2 not wired)` | only when `DMS_L2_ENABLED=1` | wire a model or unset the flag |

### Post-answer states
Not layers, but they change what the user sees:

| State | Trigger | Disclosure |
|---|---|---|
| `truncated` | `len(rows) >= 1000` and true total is larger | answer text is prefixed `"N rows match; showing the first 1000."` |
| skill graduation | any `certified`/`governed_metric`/`query_skill` answer | question + metric + params written to `dms_query_skills` |

---

## 3. What the API actually returns

`DMSQueryResponse` (`CortexOS/api/dms_query.py`) does **not** declare
`layer`, `badge`, `metric_id`, `total_count`, `truncated`, `assumptions` or
`suggestions`. FastAPI drops undeclared fields, so:

- `layer` / `metric_id` / `assumptions` reach the UI **only** because
  `_honest_plan` also copies them into `query_plan`, which is an open dict.
  Read them from `query_plan`, not from the top level.
- `total_count`, `truncated` and `suggestions` do **not** reach the client at
  all. Truncation survives only as prose inside `answer`; abstain suggestions
  survive only as prose. On abstain, `query_plan` is `{}`.

Verified live:

```
"Show expired items"  → query_plan.layer=governed_metric, metric_id=expired_items, conf=0.95
                        top-level total_count = ABSENT (engine computed 400)
"Berlin office 1997"  → route=needs_clarification, query_plan = {}
```

Fixing this is a response-model change, not an engine change.

---

## 4. The `query_skill` layer is write-only

Measured 2026-07-27 against the live skill store (42 stored skills):

| Paraphrase | Best stored match | Score | ≥ 0.72? |
|---|---|---|---|
| `show expired stock` | `show expired items` | 0.667 | no |
| `warehouse with most free space` | `most free capacity` | 0.516 | no |
| `what expired` | `show expired items` | 0.408 | no |
| `which supplier is risky` | `…spend by supplier country` | 0.354 | no |
| `carrier delays breakdown` | `delayed shipments by carrier` | 0.289 | no |
| `list overdue shipments` | `which shipments are delayed?` | 0.289 | no |
| `stock below minimum` | `which skus are below reorder level?` | 0.236 | no |

The layer is squeezed from both sides:

- **From above** — it runs *after* the deterministic L1 router, so any phrasing
  close enough to score ≥ 0.72 was already answered by a metric. The skill layer
  only ever sees questions L1 rejected.
- **From below** — `text_embedding` is a bag-of-words hash, so it carries no
  synonymy at all. Real paraphrases of L1 misses score 0.24–0.67, under the bar.

It therefore contributes **zero answers** while continuing to write a row per
successful query. `tests/dms/test_q2_answer_engine.py` proves the layer *works*
by constructing a hit directly; it does not prove the layer is *reachable*.

Two honest options: give it real sentence embeddings (then it becomes the L1.5
recall layer it was meant to be), or delete the read path and keep the table as
a usage log. Leaving it as-is means shipping a learning loop that does not learn.

---

## 5. Reproducing this map

```bash
python -m bench.accuracy      # 36 golden questions, exact phrasings
python -m bench.paraphrase    # 85 paraphrases of the same 36 intents
```

`bench.accuracy` measures whether the router answers the questions it was built
against. `bench.paraphrase` measures whether it answers the same questions asked
differently — the number that predicts behaviour on real traffic.

> Run both with `DMS_READ_ONLY_QUERIES=1`. Without it, a live API process holds
> DuckDB's exclusive read-write lock on `data/dms_demo.duckdb` and benchmark
> items fail at random. See `docs/dms/FOUNDATION_AUDIT_2026-07-27.md` §3.
