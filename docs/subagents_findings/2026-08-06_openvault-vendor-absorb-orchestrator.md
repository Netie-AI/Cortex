```yaml
keywords: [vendor-learn, openship, OmniRoute, FreeBuild, FreeRoute, steal-not-fork, human-test-gates, auto-orchestrator, Coolify, Netlify]
main_idea: "Clone vendors to steal buyer-job patterns into FreeBuild/FreeRoute under OpenVault custody/gate; auto-orchestrate tickets until HT1–HT5 human gates."
models: [grok-4.5, composer-2.5]
workflow: prd-agent -> epic-agent -> ticket-runner -> loop
reuse: golden_rule
status: verified
cite: agent: 5a16d098-f596-4d11-bd23-2dc32882e4d2
repo: OpenVault
date: 2026-08-06
```

# Vendor absorb + auto-orchestrator

## Main idea

- openship → FreeBuild steal (adapters, deploy UX, env quoting); never ship openship binary
- OmniRoute → FreeRoute steal (free-tier honesty, fallback refuse, budget UI); never ship OmniRoute binary
- Auto loop: ticket → adversary → next; stop only on HT1–HT5

## Golden rule

> Steal buyer jobs from `vendor/openship` and `vendor/OmniRoute` into OpenVault FreeBuild/FreeRoute; never fork-rebrand; auto-run until a HUMAN_TEST_GATE.

## HUMAN_TEST_GATES

HT1 live deploy · HT2 live FreeRoute keys · HT3 unseal UX · HT4 Phase0 Cortex · HT5 secrets-at-ship
