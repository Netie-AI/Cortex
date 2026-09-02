---
keywords: [openvault, omniroute, freeroute, coverage, usage, playground, skip]
main_idea: Coverage banner and health sparkline ALREADY. Remaining DISTILL is vault-row /api/usage tokens then a thin /playground. Skip compression, DRR, a2a, mcp, 250 providers. E:\OmniRoute absent; analog is E:\Netie\myOmniRoute only.
models: [cursor-grok-4.6]
workflow: 2026-08-28_omniroute-port-remaining
reuse: golden_rule
status: verified
cite: distill: E:\Netie\TAS\TAS-OPENVAULT.md
repo: OpenVault
date: 2026-08-28
---

# OmniRoute PORT remaining (2026-08-28)

PREFLIGHT: HIT. Analog `E:\Netie\myOmniRoute` only. `E:\OmniRoute` absent. No Next/Electron vendor.

## ALREADY (do not rebuild)

1. Coverage prompt + register_url -- vault page banner + GET /api/providers/coverage + startRegister
2. Health sparkline -- KeyHealthSpark + health_store + GET /api/keys/{id}/health

## DISTILL next (ticket order)

1. Per-key usage -- GET /api/usage?api_key_id= on vault row. Tokens only, priced=false. Skip OmniRoute costs/quota-share UI.
2. Playground -- lift proxy smokeChat to /playground same POST /v1/chat/completions. Skip PlaygroundStudio presets/tools/compare.

## SKIP

compression/RTK, quota-share DRR/combos, a2a/cloud-agents, mcp/tools/plugins, ~250-provider catalog (OV ~16 is intentional).
