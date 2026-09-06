# Crew Assign-task router (OpenVault destinations)

- **Date:** 2026-09-06
- **Keywords:** crew, assign, destinations, openvault, unarmed, human_stop, airgpt, claude-code, cursor-cloud, epic-116
- **Main idea:** `GET/POST /crew/assign` routes a brief to Crew / Claude Code / Claude App / Cursor Cloud / local model / AirGPT. Credentials stay on the existing OpenVault-armed Crew router. Unarmed refuses. Control GET-displays `assign_map` only. HUMAN_STOP blocks live SSH and cloud-agent spawn. Preview destinations declare a handoff; they do not invent a live session.
- **Verify:** `python -m pytest tests/test_crew -q` (256 passed locally after rebase onto #193 / a26f517d)
- **Does not prove:** live `:8020` until founder restart; Claude Code SSH; Claude App API; Cursor cloud spawn; AirGPT `:8765` in this environment. GitHub CI not run from this agent.
- **Cite:** parent Cortex#116. Rebased onto CREW-LIFE-HARDEN #193 and CREW-FACTS-MD #192. Did not attach freeze lanes #4/#41-#44.
