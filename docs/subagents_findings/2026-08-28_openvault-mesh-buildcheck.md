---
keywords: [openvault, airgpt, allcheck, cursor, anthropic, uacc, windows-mcp, computer-control]
main_idea: AirGPT allcheck now probes E:\OpenVault tree + :5000 health/chat. Cursor/Claude keys are not on disk. Crew master switch is on; arm UACC/windows-mcp; mutating still Confirm.
models: [cursor-grok-4.6]
workflow: 2026-08-28_openvault-mesh-buildcheck
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-08-28_crew-freeroute-cursor-gap.md
repo: Cortex
date: 2026-08-28
---

# OpenVault mesh in AirGPT allcheck (2026-08-28)

PREFLIGHT: HIT - crew-freeroute-cursor-gap, crew-openvault-desktop, openvault-loopback-no-bearer.

## Main idea

`E:\OpenVault` is the live tree (`D:\OpenVault` missing). AirGPT `allcheck.py` now fails closed on a missing tree or dead `:5000 /api/healthz`, and warns (does not fail) when chat hops are unhealthy. Loopback chat needs no Bearer. Cursor and Anthropic are not in env, registry, or the vault; Groq/OpenRouter/Google already are. Do not invent keys. Computer control: `CORTEX_COMPUTER_CONTROL=1` + arm UACC/windows-mcp; mutating tools stay Confirm.

## Golden rule

> Pin `OPENVAULT_HOME=E:\OpenVault\.openvault`. Paste Cursor/Claude in OpenVault Providers. Arm laptop MCP locally. Never silent-auto-click.

## Live evidence (2026-08-28)

| Surface | Result |
|---|--------|
| OV tree | `E:\OpenVault` present; `D:\OpenVault` absent |
| `:5000 /api/healthz` | 200 |
| `POST /v1/chat/completions` auto | 200, model `openai/gpt-oss-120b` |
| Vaulted | groq, openrouter, google (SEA_LION custom, missing base_url) |
| cursor / anthropic | configured=false; not in env.local, User/Machine env, Cursor settings |
| Crew `:8020 /crew/mcp` | master on; after pin `mcp<2` + `windows-mcp serve`: **uacc ready 68 tools**, **windows-mcp ready 20 tools**. Mutating still Confirm. |

## Do not

- Auto-register NVIDIA/Ollama/OmniRoute accounts.
- Print or paste secrets in chat.
- Strip mutating Confirm or set in-process PyAutoGUI execute.
- Kill Constructor `:8010`.
