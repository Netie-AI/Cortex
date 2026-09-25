# Finding: env-direct transport and the first live 52-question DMS prove

- **Date:** 2026-09-25
- **Keywords:** env-direct, CORTEX_MODEL_TRANSPORT, provider keys, gemini, nvidia, kimi, mistral 429, cerebras 402, live prove, curated_ceo, ontology_plan, insights timeout, 429 storm, schema drift, #211, #272
- **Main idea:** Cortex can now spend provider keys from the process env behind an explicit opt-in. Every stamp says so. The first live 52-question prove through DMS shows that connecting a model made DMS *worse* as shipped: 18/52 answered with the model armed, against 28/52 unarmed. The root causes are timing and schema, not the model.

## Expected vs actual
- **Expected:** arming a model raises DMS answered/ontology_plan above the unarmed baseline.
- **Actual** (Cortex b75b3b5, DMS 0577275, curated_ceo n=52; the same judge as `score_curated.py`):

| run | answered | from model SQL | WRONG | ABSTAIN (timeout) | P0 | p50 / p95 s |
|---|---|---|---|---|---|---|
| unarmed baseline | 28 | 0 | 15 | 9 (0) | 0 | 0.6 / 0.7 |
| armed, DMS as shipped (8 s) | 18 | 1 | 13 | 21 (13) | 0 | 1.4 / 8.8 |
| armed, DMS limit 120 s (diagnostic) | 26 | 6 | 17 | 9 (0) | 0 | 21.4 / 47.7 |

## Repro
1. `export CORTEX_MODEL_TRANSPORT=env-direct` with GEMINI_API_KEY / NVIDIA_API_KEY set. Then `CORTEX_FREEROUTE_MODELS=google:gemini-3-flash-preview,nvidia:moonshotai/kimi-k3 DMS_L2_ENABLED=1 PACK=dms uvicorn CortexOS.api.main:app --port 8010`.
2. Start the DMS API against it with a local steward key and `DMS_HARNESS_ASK_PATHS=1`.
3. POST `/v1/chat/ask` with `ask_path=generative` for each curated_ceo question, then score with the `score_curated.py` judge.

## Root-cause classes
1. **Consumer timeout shorter than a producer call** (DMS side):
   - DMS `INSIGHTS_ASK_TIMEOUT_SECONDS=8`.
   - A Cortex generate makes 2-6 sequential model calls and takes 13-53 s.
   - When generate times out, DMS returns `insights_timeout` before its no-model fallback runs.
2. **No cancellation.** Abandoned requests keep spending. In run A this caused 75 HTTP 429s. The router kept re-picking the 429ing provider, and a model was only marked ineligible after 3 straight refusals.
3. **Ontology drifted from the executing warehouse, with no check.** `packs/dms/ontology/*.yaml` names columns that are not in the warehouse. In the 120 s run, DMS EXPLAIN rejected 10 of the 18 model answers.
4. **Telemetry overwritten.** `insights/routes.py` re-stamps the answer without the RouteStamp, so the served model never reaches DMS.
5. **Custody surfaces hardcoded to "openvault":** `insights/keys.py`, `crew/freeroute.py`. The second file is behind the #215 lane guard.

## Security review of b75b3b5
Fixed on this branch:
- The kill switch `CORTEX_FREEROUTE=0` did not stop env-direct. Fixed in aa98768, with a test that fails on the base.

In round 2, tests are written against each:
- **P1:** a relayed `ov_` bearer spent the operator's env keys.
- **P2:**
  - a runtime env write could flip the opt-in;
  - provider keys were inherited by child processes;
  - the leave decision was not stamped.
- **P3:**
  - redirects were followed;
  - response size was unbounded;
  - partial keys were not redacted.

## Invariants
- A silent fallback is a lie. Custody must show on every stamp.
- Assert on the artifact the customer receives: the DMS envelope, not the Cortex SQL.
- Fix the root-cause class: drift needs a drift test, not a per-column patch.
- A control that blocks legitimate work is a failure: the 8 s timeout blocked every model answer.

## Does not prove
- Studio UI.
- `score_curated.py --prove-path`.
- Whether the gemini 429s are a daily quota or a per-minute limit.
- That results on the 15-row demo warehouse generalise.
- The Crew provider wiring (built and verified live, but held: it trips the #215 dual-write guard on `crew/config.py` / `crew/llm.py`; it needs the lane owner).
