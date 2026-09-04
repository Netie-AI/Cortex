Ontology-foundry: client loop on Constructor + Cortex YAML + DMS. P1 parked.

When: Palantir, Foundry, AIP, ontology, semantic layer, Constructor canvas,
client intake, warehouse insights, govern agents, 24/7 answers.

Law: `D:\Netie\Internal\Workflow\FUTURE_BUILD_ASSET_GUIDE.md` + WP-003.
Do not unpark P1. Do not clone Foundry, n8n, Cogitorium, or Semantica.

Build order (few agents, WIP 2):
1. Understand -- live Crew sidecar `http://127.0.0.1:8023/` (hung converse `:8020`, do not kill).
   SWAP `packs/dms/semantic_layer.yaml` terms. Pointer confirm: Control
   `http://127.0.0.1:8040/#pointer`. Keys: OpenVault `:5000`.
2. Constructor -- `http://127.0.0.1:8040/constructor/` or `/cortex/constructor/`.
   Prompt `understand this company` / `define data` / `govern agents` /
   `business insights`. Ghost first. Live run only on `/cortex`.
3. Orchestration -- Crew queue + wakes. Cortex `run_dag` only. Control GET belt.
4. Ontology -- `object_types.yaml` + `link_types.yaml` name-parity with semantic_layer.
5. Workflows -- Constructor kinds ontology, insight, foundry, app, tool_call.
6. Shell -- DMS Ontology Loop tab. OpenIDE display Control `GET /v1/openide`.
   Do not start AirGPT `:8765` unless asked.
7. 24/7 -- POST `/crew/wakes` note=`catalog` (expands to cortex_ask catalog).
   Crew tick fires it. Control `/v1/insights` display only. Watchdog stays listed.

Measure:
`D:\Cortex\.venv\Scripts\python.exe -m pytest tests/dms/test_constructor_graph.py tests/dms/test_ontology_registry.py -q`

BAN: n8n, grok-bot copy, Guaca AGPL, OpenWillow GPL, leaked Claude Code, AP 665.
Control POST `/v1/run` stays 405. Do not kill hung `:8020`.
