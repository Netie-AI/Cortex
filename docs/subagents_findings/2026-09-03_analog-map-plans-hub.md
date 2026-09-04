---
keywords: [analog, tas, control, plans, constructor, venue, openide, search, mybot, opencode, mem0, palantir, n8n]
main_idea: Analog clones stay frozen on D:\. Control GET /v1/plans displays leftover DISTILL/PARK/BAN. Constructor Place-Venue-Contact-Lead generate is live on Cortex :8011. OpenIDE search ranks tokens not first-grep. Palantir P1 parked. COPY none of grok-bot/n8n/GPL/leaked CC.
models: [cursor-grok-4.6]
workflow: 2026-09-03_analog-map-plans-hub
reuse: golden_rule
status: verified
cite: distill: D:\Netie\TAS\README.md#analog-source-map
repo: cortex
date: 2026-09-03
---

# Analog map + Control plans hub (2026-09-03)

PREFLIGHT: PARTIAL
reuse: `2026-08-28_analog-measure-four-lanes.md`, `2026-08-28_palantir-ontology-distill.md`, `2026-08-28_constructor-activepieces-skip.md`, `2026-08-28_crew-analog-copy.md`
spawn: skip

## Main idea

Do not rebuild clones into a second ecosystem. Map each prior tree onto the TAS product, then display leftover DISTILL/PARK/BAN on Control `GET /v1/plans`. Live paths on this laptop are `D:\`.

## Golden rule

> Analog clones stay frozen. Control displays remaining work. Cortex runs Constructor generate. Palantir P1 stays parked. Do not kill hung Crew :8020.

## Verify

```
$env:PYTHONPATH='D:\NetieControl'; python -m pytest D:\NetieControl\tests\test_control_stays_plane_4.py::test_v1_plans_maps_analog_trees_and_does_not_assign -q
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/dms/test_constructor_graph.py -q
python -m pytest D:\AirGPT\OpenIDE\tests\test_search_query_rank.py -q
Invoke-WebRequest http://127.0.0.1:8011/health
Invoke-WebRequest http://127.0.0.1:8040/healthz
Invoke-WebRequest http://127.0.0.1:8040/v1/plans
Invoke-WebRequest http://127.0.0.1:8040/v1/prompts
Invoke-WebRequest http://127.0.0.1:8023/crew/health
Invoke-WebRequest http://127.0.0.1:5000/api/healthz
```

Live 2026-09-03 later: Cortex `:8011` PACK=dms. Control `:8040` `/v1/plans` + `/v1/prompts` + `/v1/insights` + `/v1/fetch`. Crew sidecar `:8023` healthy (isolated `data/crew-8023`). OpenVault `:5000` `/api/healthz` + `/api/ship/library`. Hung converse `:8020` left alone. OpenIDE `:8765` not started (R-0015). Pointer confirm-gate on disk; Control does not start Electron.

## Shipped this wave

- Constructor engine generate knows Place-Venue-Contact-Lead; ghost fetch names `maps.venues` with no invented DuckDB rows. No extra YAML objects.
- OpenIDE `search_files` ranks distinct query tokens. Empty box is not match-everything. Composer paste detects GitHub repo URLs. Clone dest stays under parent. Push-help never types credentials.
- OpenIDE S5: prompt layers doc already existed. Token/tool-loop coalescing in `OpenIDE/stream_coalesce.py` + `openide.js` rAF. Local agent loop advertises OpenAI/Cursor `type=function` schemas and parses `name`/`arguments`/`tool_calls`.
- Control analog lane 11 note names the token rank. Lane 19 note names S5.
- Control `GET /v1/insights` shows Cortex pack + constructor gated without probing ontology (single-worker queue). Palantir P1 parked. Default Cortex probe `:8011`.
- Control sidecar names wakes none vs unread. Tick stays on Crew. Control does not POST wakes.
- Live `POST /cortex/constructor/generate` Place-Venue-Contact-Lead 200 with viewer key.
- Control OpenVault ship probe is `GET /api/ship/targets` (local, ~0.3s), not `/api/ship/library` (GitHub + repo list, ~2.5s, over `OPENVAULT_USAGE_WAIT_S` 1.5s). Slim keeps tabs/openship_effective/HT1/target_count. Drops repos, tokens, adapter paths, live_public_url. GET `/` paints `tabs=folder github url upload`.
- Live warehouse `POST /cortex/constructor/run` Ghost-off: inventory connector + app, `table=inventory`, 7433 rows, actor `api_viewer`.
- Segment reuse is now the PRD/writer law: TAS analog map + Control paste `prd-agent`. Extend live files. If stuck, DISTILL one analog segment. Do not redesign UI tokens/layout. Analog clones stay frozen.

## Still open

- Palantir P1 parked. Palantir-lite is Constructor generate (define data / govern agents / business insights), not a Foundry product.
- Crew `:8020` converse is founder rebind (YOU step 8). Do not auto-start. Sidecar `:8023` is the live tick. Empty wakes is none, not unread. Do not invent a standing wake.
- Ticket Runner still has to seat a writer on a CLAIMS item for self-evolve. Control `GET /v1/fetch` names `next=Netie-AI/Cortex#2` and POST `/v1/run` stays 405. Do not write UNSEATED Cortex#2.
- Analog leftover `analog_next` is lane 9 OpenManus DISTILL (quality-terminated loop). TAS: lanes 9-20 do not open a new WIP except Control display. SKIP product.
- OpenVault HT1-HT5 HUMAN_STOP. Pointer Electron not started. Do not start Pointer/:8765/:3010 from Control.

## Shipped 2026-09-03 later (white paper + asset guide)

- `D:\Netie\White Paper - Why\WP-003-reuse-the-estate-do-not-rebuild.md` -- Palantir-class job is restitch, not Foundry.
- Constructor Palantir-lite seeds on `D:\Constructor` and `CortexOS/constructor_skin`: Define data, Govern agents, Insights. `generateLocal` matches engine govern/define. Control `/constructor/` shows the buttons. Engine `/cortex/constructor/` still key-gates to login. P1 parked. `tests/dms/test_constructor_skin_palantir_seeds.py`.

## Shipped 2026-09-03 later (HITL catalog honesty)

- Lane 1b OpenWorker HITL/lease: SHIPPED. Engine already has `queue.py` leases + `approvals.py` tighten-only. SKIP `ee/`.
- Lane 3 Paperclip: SHIPPED heartbeat labels on `GET /v1/plans` and `GET /v1/fetch` (no extra HTTP). REFUSE React/:3100.
- Lane 8b Rakazo: SKIP. Stop CSS paste.
- `GET /v1/insights` palantir_lite names Constructor generate paths. Control does not POST generate.
- DMS Ontology on live `D:\DMS` (`:3001` this session; `:3000` is another lane `DMS-epic020`): **Constructor canvas** opens Control `/constructor/`. Click Define data fills `define data for inventory` and compiles ontology/insight/foundry locally. P1 parked.
- Constructor n8n-class live on `:8011`: issue-key -> generate define/govern/insights 200, catalog 200, run inventory fetches 7433 rows. Control `GET /v1/fetch` Crew sidecar stamps `wakes present n=1`. POST `/v1/run` 405. Cortex#2 UNSEATED. DMS UI `:3001` and API `:8090` down (`:3000` is another lane, leave it). Hung `:8020` left alone.
- Live Constructor n8n-class path on `:8011` with viewer key: engine skin Palantir-lite seeds, generate warehouse, ghost ok, run 200 actor `api_viewer` inventory 7433 rows.
- Laptop `GET /v1/fetch` `working` tray: Constructor, Crew sidecar `:8023`, OpenVault free `priced=false`, OpenIDE, Pointer confirm-gated. analog_next now lane 10 Orca DISTILL (display only). Grok offloaded. POST `/v1/run` 405.
- Live Cortex `POST /cortex/constructor/generate` govern agents -> `action=agent.checked` kinds connector,ontology,agent,audit. `issue-key` 200 (token not printed). NetieEstate24x7 schtask Ready.

