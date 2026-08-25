---
name: crew-ship-gate
description: Adaptive production ship-gate for github.com/Netie-AI repos via Cortex Crew. Use when the user says before shipping, production ready, ship gate, SQL injection, WCAG, CI/CD, or govern every repo.
---

# Crew ship-gate

## Before coding

1. `estate_status` (or read `CortexOS/crew/estate.py` catalog). Surfaces decide domains.
2. `ship_gate repo=<slug|all>`. Fail closed. Skip is not pass.

## Adaptive

| Kind | Score |
|------|--------|
| engine (Cortex) | Security, tests, CI, contract. Demo UI is not WCAG. |
| keys (OpenVault) | Auth, secrets, rate limit. Not a marketing site. |
| web-only public | Tests + privacy + a11y. |
| rtl (OpenHBM) | sim/lint/formal. No WCAG. |
| empty (AIM) | FAIL. Cannot ship. |
| missing (AirGPT, DMS, chatbot, Pointer, Netie, OMI) | FAIL. Create private Netie-AI origin. |
| accidental (jian-hong/Vking) | FAIL. Canonical is the org repo. |

File presence is not SOC2/HIPAA/GDPR.

## Spawn

A sweep is one Gate. Spawn a job-named teammate only for FAIL domains. Do not spawn six specialists. Do not auto-merge. Human is money/decision.

## Verify

```bash
python -m pytest tests/test_crew -q
```
