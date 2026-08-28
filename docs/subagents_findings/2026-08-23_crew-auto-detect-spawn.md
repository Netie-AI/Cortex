---
keywords: [crew, detect, spawn, capability, verifier, grants, auto-detect]
main_idea: Crew pills are capability templates. Detect the job. Spawn job-named teammates with allow/deny tools and optional verifier. No LangGraph. No Grok AppData. OpenVault holds keys.
---

# Crew auto-detect spawn

## Main idea

Fixed specialist chips as spawn buttons were wrong. `CortexOS/crew/detect.py` keyword-matches the user turn onto capability templates in `roles.py`. The Manager still calls `spawn_agent`; detect injects a directive (including "do not spawn"). Names are job-specific. `capability=` copies the prompt. Shared MCP pool; `allow_tools`/`deny_tools` per agent. `verify=true` without criteria is skipped. With criteria, runtime starts `{name}-verify`. Tone default is `skills/tone.md`. Models via OpenVault; grok-fast rewritten to grok-4.6. Readable chat dumps ingest from `data/crew/drops`; AppData is blocked.

## Golden rule

> Detect, do not roster. Spawn 0 on ping/pong. Verifier needs criteria. Crew A2A is the graph. Engine gen_cfsm stays on the answer plane.

## Verify

```bash
cd D:\Cortex-crew
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_crew -q
```

## Overnight (keep-alive only)

```powershell
powershell -NoProfile -File D:\Cortex-crew\scripts\night_watch.ps1
# existing schtasks NetieEstate24x7 every 15 min
# do not spawn Cursor cloud chats; do not implement SEATED/HELD tickets
```
