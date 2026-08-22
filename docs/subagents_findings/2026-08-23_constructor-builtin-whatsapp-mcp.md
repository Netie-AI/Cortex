```yaml
keywords: [constructor, memory, whatsapp, mcp, auto-caller, airgpt, builtin-app, p16]
main_idea: "WhatsApp is draft-only; community WhatsApp MCP is found then P16-parked; Constructor is a hosted Cortex builtin at /cortex/constructor/ with MCP auto_caller.pick plus AirGPT sidecar tools; RawKnn persist needs check_same_thread=False for TestClient/API threads."
models: [grok-4.6]
workflow: none
reuse: golden_rule
status: raw
cite: distill: D:\Cortex\skill_distill\captures\2026-08-22_cursor_constructor-powered-by-cortex.md
repo: Cortex-constructor-mount
date: 2026-08-23
```

# Constructor builtin + WhatsApp MCP challenge

PREFLIGHT: PARTIAL
reuse: constructor-ontology-connectivity-gap (mount now exists; Palantir P1 still parked)
spawn: skip

## Main idea

- Persistent memory: RawKnnStore 256-record close/reopen plus API upsert/query across a new store instance. sqlite must use `check_same_thread=False` or FastAPI TestClient dies.
- WhatsApp connector challenge: `draft_whatsapp` has no send libs. `find_mcp("whatsapp")` hits community servers. `auto_caller.pick` routes to `POST /dms/brain/whatsapp` and marks community MCP `p16_parked`.
- Constructor is `builtin-constructor` in app_store: approved, hosted, no extra port, not deletable, start returns `/cortex/constructor/`.
- AirGPT: marketplace `mod-constructor`, tools `constructor_ghost` / `auto_caller_pick` via cortex_client.

## Keywords (search)

`constructor`, `memory`, `whatsapp`, `mcp`, `auto-caller`, `airgpt`, `builtin-app`, `p16`

## Verify

```
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/dms/test_memory_persist_stress.py tests/dms/test_auto_caller_whatsapp_mcp.py tests/dms/test_builtin_constructor_app.py tests/dms/test_mcp_routes.py -q
```

AirGPT:

```
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_constructor_airgpt_tools.py tests/test_chat_tools.py -q
```

## Do not

- Unpark P16 third-party MCP clients
- Add Twilio/Baileys/pywhatkit send
- Spawn a second Constructor HTTP process
- Point landing OPEN CONSTRUCTOR at /contact/
