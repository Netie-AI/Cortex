# Cortex Crew landing bakeoff -- vote sheet

Judging: generate a 3D asset website to promote Cortex Crew.
Fail: solid-color boxes as the hero, pasted screenshot, image standing in for 3d.

| Lane | How it was drawn | Model / path | Open |
|------|------------------|--------------|------|
| A oneshot | This Cursor session, no Crew loop | cursor-grok-4.6 (chat) | `a-oneshot/index.html` |
| B freeroute-auto | Live `POST :5000/v1/chat/completions` model=auto | stamped in `b-freeroute-auto/META.json` | `b-freeroute-auto/index.html` |
| C playbook | analog-surface refuse rules, CSS 3D only | cursor-grok-4.6 applying Crew skill | `c-playbook/index.html` |
| D orchestrated | detect Surface + verify + spawn story on the page | cursor-grok-4.6 + Crew detect text | `d-orchestrated/index.html` |

Vote in `index.html` (localStorage). Do not burn a second Cursor API key until OpenVault has CURSOR_API_KEY / ANTHROPIC_API_KEY vaulted.

cite: docs/subagents_findings/2026-08-28_crew-freeroute-cursor-gap.md
