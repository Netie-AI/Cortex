# Cortex API - DMS generative-ask and AirGPT skin

Cortex is the compute brain. DMS is a consumer. AirGPT is a skin. Keys stay in
OpenVault. This is not a `cortex-contract` version bump: `/v1/contract/*`
(ask/submit/ledger) stays 1.2.0. Insights is a sibling surface on the same
engine.

Does **not** prove live `:5000` / `:8020`. `live_5000_ci` is always false.
Does **not** claim CoT COMPLETE. Climb % stays the DMS #180 baseline
(gen 57.69% / exact 38.46% WRONG=0 @ `d2f116a6`); Cortex does not invent a
better number.

## Paths

| Who | Call |
|-----|------|
| DMS generative-ask | `POST /v1/insights` `{intent or question, ask, generate}` |
| AirGPT skin | same, or `POST /dms/sidecar/insights` (viewer API key) |
| Crew chrome | `POST /crew/insights` (alias of the same `run_insights`) |
| Map (no numbers) | `GET /v1/insights` |
| Ontology only | `GET /v1/insights/ontology?q=` |
| Identity (no token) | `GET /v1/insights/identity` |
| Key posture (no secrets) | `GET /v1/insights/keys` |
| L0/L1 governed ask | `POST /v1/contract/ask` (unchanged) |

Envelope status is **CERTIFIED | ABSTAIN | REFUSE**. Numbers are never invented.
`generate=true` is NL then ontology then CoT/route/improve then SQL then
validate. Validated SQL is **ABSTAIN**, never CERTIFIED. Unarmed FreeRoute is
**REFUSE**.

`plan_source` is a producer stamp for DMS Studio. `ontology_plan` only when
this answer's SQL came from that generate path. Otherwise `other`. A request
`mode=ontology_plan` is not copied onto the stamp. `query_sql` is set only
alongside honest `ontology_plan`. This does not re-prove live DMS #231.

Lane stamps (INSIGHTS-LANE-INVERSION-01, no new fields). A model wrote the
SQL: `plan_source=ontology_plan`, `model_called=true`, `served_*` copy the
vault RouteStamp of that call; this includes SQL that `l2_serve` executed
after its gates (`query_sql` is then the SQL that ran). A certified query
served as stored with no model call: `plan_source=other`,
`model_called=false`, `served_model` null, `served_reason` and
`generative.check` = `certified_query:<id>`; the SQL is in `sql_used` and
`generative.sql`, not `query_sql`.

Stale lake (#402, no new fields). SQL that names a column with a declared value
domain (`packs/dms/ontology/object_types.yaml`, e.g. `transactions.txn_type`)
is executed only when the lake stores the declared canonical values. A lake
built before the declaration, or by another loader, that still holds a source
spelling (`OUT`) or a value the declaration does not list is an abstain named
`lake_value_domain_stale:<table>.<column>` in `answer` and `assumptions`
(`answer_step=value_domain` on the L2 serve path): no rows, no figure, and no
stored value in the text. The check sits in the query seam, so
`POST /v1/contract/submit` SQL that names such a column on such a lake is
`status=sql_gate_abstain` with the same reason. The plan+SQL ladder
(`CORTEX_PLAN_SQL=1`) stops on it with the same reason (`badge=abstain`); it is
not fed back to the model as a retryable gate error. The check is kept per
read-only warehouse instance, so a lake renamed over the path is checked by the
first instance that reads it. Rebuild the lake (`load_inventory_csv` or the
lakehouse silver sync) to clear it. On `generate=true`, SQL whose sales measure
pins a value outside the declared values is a named retry,
`measure_filter_undeclared`, and is never returned as validated.

## Key posture (local vs cloud)

OpenVault is the only vault. Cortex does not store provider secrets. Callers
do **not** send `sk-` / `gsk-` / raw provider keys.

| Mode | What Cortex sees | What the caller sends |
|------|------------------|------------------------|
| Local hops | OpenVault-custodied ground models (`ollama`, `this-pc`, `llama.cpp`, …) | loopback peer with no Authorization, or `Authorization: Bearer ov_...` |
| Cloud hops | OpenVault-custodied provider APIs (groq / deepseek / …) | same: `ov_` or loopback. Never the provider key. |
| Unarmed | named refusal | nothing to spend |

`GET /v1/insights/keys` returns `local_keys` / `cloud_keys` as booleans plus
hop provider names. It never returns tokens, LIVE_KEY, or provider secrets.
LIVE_KEY freeze (`119691f2c637`) stands; this API does not rotate or paste it.

A non-loopback caller that wants `generate=true` while FreeRoute is armed must
present its own `ov_` key (A-0009). Cortex's `CORTEX_FREEROUTE_TOKEN` is never
lent.

## Honesty leftover

- `POST /dms/query` and Crew chat still spend Cortex's credential for an
  unauthenticated local caller (PARKING_LOT P24.4 remainder). `/v1/insights`
  generate does not.
- G4 litellm / brain / suggest env-key hosts stay P24.1. This slice does not
  expand them.
- `cot_climb` is INCOMPLETE. Fixture coverage is not a replacement for the
  DMS #180 baseline. Like-with-like requires the pinned 26 ids @ `d2f116a6`;
  a 5-item fixture is not that corpus. Think is consumed in the SQL prompt.
  Covering increment: G1 plan consumed in think; this_run.exact scores
  certified gold when present and stays 0.00% on empty complete -- never
  invent 38.46%.
