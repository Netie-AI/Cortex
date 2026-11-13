# DMS: first live-model numbers on a new customer's dataset, and what stood in the way

Date: 2026-11-13. Repos: DMS, Cortex, OpenVault. No repository file was changed: everything ran from scratch directories on loopback.

## Setup

The founder approved running a copy of OpenVault in the cloud session with the provider keys already in that environment. OpenVault's custody API ran on 127.0.0.1:5000 with a scratch vault (NVIDIA and Google keys added through `POST /api/keys`; the bulk env import was not used because it would also have taken GitHub and Cursor tokens). Cortex core in dev mode, a DMS API in live ask mode, and a logging pass-through between them so a refused submit's reason could be read. FreeRoute served `nvidia/nemotron-3-ultra-550b-a55b`.

Dataset and questions: the seeded four-table retail set and seven questions from `2026-11-12_dms-byo-data-experiment.md`, as CSV files in one Space and as a PostgreSQL source with declared keys in another.

## Expected vs actual

Expected: with a model reachable, most single-table questions answer and joins either answer over verified links or abstain.

Actual (n=7 per row, counts only; seven questions support no rate claim):

| Space | Configuration | Correct | Wrong | Abstain |
|---|---|---|---|---|
| PostgreSQL source | existing flags on | 2 | 0 | 5 |
| PostgreSQL source | shipped defaults plus wiring | 1 | 0 | 6 |
| CSV files | existing flags on | 0 | 0 | 7 |
| CSV files | shipped defaults plus wiring | 0 | 0 | 7 |

No wrong figure was served in any live run. The two correct answers: "How many orders are there?" (300) and "Which product has the highest total quantity?" (P015, 276), the second through a join over a verified link.

"Existing flags on" means `DMS_SCHEMA_CONTEXT`, `DMS_AI_FIRST`, `DMS_CLOOP_B`, `DMS_INSIGHTS_CALL_CAP=4`, and on Cortex `CORTEX_GRANTED_CATALOG`, `CORTEX_PLAN_SQL`, `DMS_READ_ONLY_QUERIES`.

## What stood between a fresh install and one correct answer

In the order they bite. "Config" means a setting fixed it; "code" means nothing short of a change does.

1. Config. No OpenVault: every ask abstains `insights_bearer_missing`.
2. Config. `DMS_INSIGHTS_TIMEOUT_S` defaults to 8 seconds; the model takes 10 to 11. Every ask abstains `insights_timeout`.
3. Config. OpenVault's leave gate needs a key from one of five named providers. An NVIDIA-only vault is denied.
4. Config. The serving warehouse file must already exist: DMS says "serving warehouse missing - will not create it". Compose sets neither warehouse variable.
5. **Dependency drift.** DMS pins `duckdb>=1.0` and now resolves to 2.0.0, which writes storage version 69. Cortex pins `<2` and cannot read it: "Trying to read a database file with version number 69, but we can only read versions between 64 and 68". It surfaced to the user as "the model step failed (IOException)". Any DMS environment or image built since DuckDB 2.0 has this.
6. Config. The ask budget (`DMS_SERVING_DEADLINE_S`) works out to 24.55 seconds: longer asks end `serving_deadline_exceeded`.
7. Config, ordering. With read-only queries on, Cortex holds the serving file after its first query and a later DMS sync cannot take the write lock.
8. **Code.** A Space built from uploaded files never reaches the model. `read_catalog` (`packages/executor/dms_executor/schema_context.py:2263-2267`) reads `information_schema.columns WHERE table_schema = 'main'`. Uploaded tables are in schema `bronze`. The catalog is empty, no schema is sent, and Cortex refuses with "no ontology path or metric for intent" in 0.05 seconds.
9. **Code.** The grounded lane ("ask about this table") cannot serve customer data: `packs/dms/generative/schema_retrieval.py` reads only the pack's semantic layer, and `sql_generator.py` caps the reply at 600 tokens, which a reasoning model spent on reasoning (content null).
10. **Code.** `build_dms_payload` has no caller, so Cortex's plan-then-solve never gets its input.
11. A false abstain: correct SQL for a single-table SUM was stopped by the text-citation check.
12. DMS reduces a refused submit to `submit_failed` and drops Cortex's reason.

## Root-cause classes

1. **An unpinned upper bound across two products that share a file format.** DMS writes the file, Cortex reads it, and each resolves its own DuckDB. Same class as import-linter 2.16 on Cortex CI the day before.
2. **A catalog read scoped to the demo's schema.** The schema context was built and tested on tables in `main`. Customer tables live elsewhere, so the feature is a no-op exactly where it is needed. The flag being on gives no hint.
3. **A gate that is skipped where it has nothing to check, masked by an earlier failure.** The rule 16 join check is skipped for Spaces with no ontology (`generative_ask.py:1597`, `:2586`). It has never mattered because of item 8. Fixing item 8 alone would open it.
4. **Defaults tuned to a faster model than the one the router picks.** An 8 second call timeout and a 24.55 second ask budget turn every live ask into an abstain.
5. **An error reduced to a code.** `submit_failed`, "IOException" and `insights_refused` each hid a precise, actionable cause that was one layer down.
6. **"No keys here" reported without checking the environment.** Two agents and the lead said so. Five provider keys were set as environment variables.

## Invariants that applied

- Assert the artifact the customer receives: the tallies come from `POST /v1/chat/ask` envelopes graded against truth computed first.
- Do not claim zero wrong below n=300: 0 wrong in 28 live asks, 25 of them abstains. That bounds nothing.
- Silent fallback is a lie: classes 2 and 5.
- A control that blocks legitimate work is itself a failure: the citation check that stopped a correct SUM.

## Agent shape

One Sonnet reader mapped how to run OpenVault from the three repos (read-only). The lead brought the chain up step by step, because each blocker only appeared after the previous one was removed. One Sonnet agent then took the running stack and the list of known blockers and found items 5, 6, 7, 8, 9 and 10. The PRD agent (session model) routed the defects. The ontology ticket (#514) runs in its own cloud session on its own branch, per the one-writer rule.

## Not verified

Whether the PostgreSQL Space's abstains would answer under a non-reasoning model pin (`CORTEX_OV_STRICT=1` was not tried). The join-gate hole in practice. Anything on a real customer's data.
