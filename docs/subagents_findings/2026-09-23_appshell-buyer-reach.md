```yaml
keywords: [appshell, buyer-reach, host, app.netie.ai, leave-machine, #237, #236, #195, #232]
main_idea: "APPSHELL-BUYER-REACH: documented https://app.netie.ai/appshell + engine GET /appshell chrome so tunnel :8020 is not the only path. OV leave-machine fail-closed. No invent CI live-host green. Not buyer COMPLETE."
models: [grok-4.6]
workflow: goal-crew-control
reuse: 2026-09-06_cortex-appshell, 2026-09-23_crew-8020-facts, 2026-09-04_c7-05-gsh-and-leave-gate
status: verified
cite: agent: appshell-buyer-reach
repo: Cortex
date: 2026-09-23
```

# APPSHELL-BUYER-REACH #237 (parent EPIC-APPSHELL-HOSTED #236)

PREFLIGHT: HIT
reuse: 2026-09-06_cortex-appshell, 2026-09-23_crew-8020-facts, 2026-09-04_c7-05-gsh-and-leave-gate
spawn: skip

## Gap

Local operator AppShell (#195) and tunnel `:8020` leftover (#232) were not a buyer path. `app.netie.ai/cortex` already existed; AppShell had no documented host path on that same host.

## Landed (branch `cursor/appshell-buyer-reach-5309`)

- Documented public URL `https://app.netie.ai/appshell` (existing host, not a new name)
- Engine-local path `GET /appshell` on the same FastAPI app as `/cortex`
- Crew alias `GET /appshell` plus `GET /crew/appshell/host` law and `/host/live` prove
- OpenVault `action=leave` `destination=appshell-host` for non-loopback bind / live public claim
- `CREW_LIVE_PROBES=0` / host-down / denied is `NOT_PROVEN` or `HOST_DENIED`
- Control stays GET F-0030
- Tunnel `:8020` leftover stays visible and NOT buyer COMPLETE

## Verify

```
python -m pytest tests/test_crew/test_appshell_buyer_reach.py -q
python -m pytest tests/test_crew -q
python -m CortexOS.crew.appshell_host
```

Local: focused 15 passed; with appshell + facts 31; `tests/test_crew` **390 passed**. CLI `python -m CortexOS.crew.appshell_host` -> `HOST_DENIED` (OpenVault unreachable, empty response). Not a GitHub CI live-host claim.

## Residual

Platform must actually route `/appshell`, `/crew.css`, `/appshell.webmanifest`, `/crew/appshell*` on `app.netie.ai` if the current proxy only maps `/cortex`. Engine mount is chrome + GET catalog/control/audit, not full Crew converse. Live DNS/TLS is founder/platform. #212 stays OPEN.

## Not this slice

CoT leftover #212. Excel/PPT #197-#200 HOLD. Freeze #4/#41-#44. Trained JEPA. Product COMPLETE. Dual-own OpenVault.
