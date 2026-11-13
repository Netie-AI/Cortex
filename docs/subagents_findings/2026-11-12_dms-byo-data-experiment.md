# DMS: a new customer's dataset reaches zero correct answers, and demo mode serves one wrong one

Date: 2026-11-12. Repos: DMS (apps/api, packages/executor, apps/ui), Cortex (core profile, dev mode). Nothing was changed: this is a measurement plus a routing result.

## Why it was run

The founder asked for DMS to be easy to point at other data ("more different data types, easy change to other data, auto ontology, the AI understands the whole procedure: join, pass, run, infer and give the result"). Before building, four read-only agents mapped the code and one ran a real experiment through the product's HTTP API.

## The experiment

A seeded retail dataset the product had never seen: customers (60), products (25), orders (300), order_items (800), as four CSVs and one four-sheet workbook, with realistic mess (8 spellings of 4 countries, currency prefixes on some prices, two blank rows above one sheet's header, 5 orders with no matching customer). The same tables were loaded into a local PostgreSQL 16 with keys declared. Seven questions with true answers computed first.

Stack: Cortex on :8010 (no model, no OpenVault), a DMS API in live ask mode, a DMS API in demo ask mode.

## Expected vs actual

| Step | Expected | Actual |
|---|---|---|
| Create a Space | works | 1 call, works (in memory: gone on restart with no Postgres bound) |
| Ingest 4 CSVs and the workbook | 8 tables with usable types | 8 bronze tables, correct row counts, **every CSV column stored as text**; country spellings classed clean; only products flagged dirty and still ingested |
| Make them askable | automatic | every receipt says `serving_sync not_attempted`, `chat_can_see_it false`; promoted silver tables cannot be granted in a Space; promote quarantined 10 of 25 products |
| Understand the data | keys and joins proposed | file Space: `derived:false`, nothing inferred. PostgreSQL source: **one call** gave 4 verified objects, 2 verified links, and the orphan link marked unverified with "5 rows reference a parent that does not exist" |
| Ask 7 questions, live mode | answers or honest abstains | 28 of 28 abstain (`insights_bearer_missing`): no model. No wrong number. Nothing tells the customer it is a setup gap. |
| Ask 7 questions, demo mode | abstain (not the demo warehouse) | 6 abstain. **1 wrong**: "total line_total by product category, top 3" returned badge `L2_VALIDATED`, "SKU-BETA at RM 8,312.50", from the demo `transactions` table, with the customer's Space id stamped on the demo sources. Truth: Food 381,397.77. Reproduced twice. |

Correct answers: 0 of 28.

## Read from code, not executed (no model here)

- A Space built from files has no ontology, so the rule 16 join check (`unverified_join_reason`) never runs for it. A model-written join over uploaded files would be served `L2_VALIDATED` with no join gate.
- At shipped defaults most of the AI ladder is off (`DMS_CLOOP_B`, `DMS_SCHEMA_CONTEXT`, `DMS_AI_FIRST`, `DMS_DEF_VALIDATE`, `DMS_VQ_RAG`; Cortex `CORTEX_PLAN_SQL`, `CORTEX_GRANTED_CATALOG`). The model does not see a file Space's columns unless `DMS_SCHEMA_CONTEXT` is on.
- A proposal and approval lifecycle for ontology items already exists (`dms.ontology_versions`, `ontology_items`, proposed / approved / revoked, steward routes). Its only proposer needs a model call per table, and approved items are tenant-wide, not per Space.
- Intake damages input silently: UTF-8 decode with replacement, two workbook sheets sharing their first 40 name characters overwrite each other, TSV and semicolon CSV are refused although the file picker offers `.tsv`.

## Root-cause classes

1. **A demo answer path that ignores the scope it is asked in.** The demo router matches wording and answers from its own tables, then stamps the caller's Space on them. Class: a fallback that borrows the identity of the thing it replaces. Rule 11 and rule 10a.
2. **A safety gate keyed on the presence of the thing it guards.** The join allowlist runs only when an ontology exists, so the Spaces with the least structure get the least checking.
3. **"Not configured" and "cannot answer" look the same to the customer.** Every abstain says the same thing whether the cause is the data or a missing service.
4. **Ingest reports success for input it changed.** A receipt that says rows landed, without saying what was coerced, replaced or dropped, is a silent fallback.
5. **A claim of inaccessibility made without checking.** At the start of this session the PRD repo was called unreachable. It was listed by `list_repos` and public. The routing rule was skipped for a whole feature on that false premise.

## Routing (PRD agent, verified against PRD-001 and GitHub)

- Phone work (dms#510): EPIC-008 (#8, closed) must reopen; same class as open #171.
- Auto ontology: the #277 lane (#281 to #284, #368, PR #461), proposed-only; its epic #178 is closed and was directed to reopen.
- AI procedure and accuracy: existing epics (#38, #369, #257, #256, #265, #132); P0 #508 first.
- More source types: an accepted amendment whose PRD text and source list were never written.
- "Platform as a service": contradicts PRD section 3 (single-tenant only). Fifth ask. Founder decision.
- The PRD's feedback ledger in git ends at F110 while DMS cites F124. Rows returned as F-TBD-19 and F-TBD-20 for the founder to append.

## Invariants that applied

- Assert the artifact the customer receives: the wrong answer was found by asking through the API and comparing to a precomputed truth, not by reading the router.
- Silent fallback is a lie: classes 1 and 4.
- Separate verified from assumed: the join-gate exposure is read from code and marked unexecuted.
- Do not claim zero wrong below n=300: live mode showed 0 wrong in 28 asks, all abstains. That bounds nothing about a model's answers.

## Agent shape

Six agents, isolated contexts. One PRD agent on the session model (the constitution runs the three named agents on the selected model). Four Sonnet readers in a workflow: intake, ontology, ask procedure, and the experiment (the only one that touched the running stack; writes only to scratch). One Sonnet verifier for dms#510 afterwards. About 0.9M subagent tokens for the workflow, 14 minutes, two agents at a time.

What needed the lead: reproducing the wrong answer before reporting it, reading the constitution (WIP limit of two epics, no standing army) before fanning out builders, and deciding that nothing gets built before the founder's decisions.

## Not verified

Any answer produced by a model. Whether the derived PostgreSQL ontology yields correct answers. The join-gate exposure in practice.
