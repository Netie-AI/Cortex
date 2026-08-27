```yaml
id: 2026-08-22_cursor_constructor-powered-by-cortex
source: cursor
date: 2026-08-22
operator: constructor-writer
prompt_used: skill_distill/prompts/ASK_CURSOR.md
distill_trace: skill_distill/DISTILL.md
status: raw
```

## Raw answer

Founder: everything must be powered by Cortex. PRD amendment: same-origin on existing :8010, Pages stays 200 brochure with 0 fetch, no github.io CORS, no new host. P17/P1/O6 stay parked. Constructor writer encoded origin gate. Cortex writer still owes constructor_graph.py + /constructor/ mount.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Constructor is a Cortex consumer skin; github.io never fetch | observed | high | rule |
| Engine UI uses existing http://127.0.0.1:8010/constructor/ not a new hostname | docs | high | rule |
| P17 hosted-API packaging stays parked; O5 sidecar plus compile-then-run is enough | docs | high | parking |
| P1 Palantir AIP and O6 stay parked under powered-by-Cortex | docs | high | parking |

## Action YAML

```yaml
action: constructor_powered_by_cortex
pages: brochure-200-no-fetch
engine_origin: http://127.0.0.1:8010/constructor/
do_not: [invent-host, github.io-CORS, keys-in-pages, P1, O6, P17-unpark]
```

## Netie implications

- Build now: Cortex writer compile adapter + StaticFiles mount. Constructor origin gate already in app.js.
- Park: P1, O6, P17.
- Tests: rg fetch empty on Pages app.js; later :8010/constructor 200.

## Citations

- distill: skill_distill/captures/2026-08-22_cursor_constructor-powered-by-cortex.md
- prd: 1a287edc-4e3a-4a63-ac08-25284f7e17b1
