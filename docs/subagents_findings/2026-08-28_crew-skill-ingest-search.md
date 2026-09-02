---
keywords: [crew, skill-ingest, web_search, github_search, save_skill, labels, analog-surface, impeccable, openvault]
main_idea: Crew can search GitHub and save labeled skills from chat. Live impeccable ingest died on OpenVault FreeRoute 400, not on missing tools.
models: [cursor-grok-4.6]
workflow: 2026-08-28_crew-skill-ingest-search
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-08-28_crew-analog-prompt-wire.md
repo: Cortex
date: 2026-08-28
---

# Crew skill ingest + analog-surface (2026-08-28)

PREFLIGHT: HIT - crew-analog-prompt-wire, crew-harness-patterns, skills-layer-planted-render.

## Main idea

Manager chat can now web_search, github_search, web_fetch, then save_skill with labels. Detect maps "add impeccable skill into skill storage" to Skills + skill-ingest, and "make a website" / "clone https" to Surface + analog-surface, without needing the word design. Live run on :8020 failed before any tool call: OpenVault HTTP 400 non-retryable (SEA_LION missing base_url, Google 503). No new third-party accounts.

## Golden rule

> Search then fetch then save_skill with what-it-is-FOR labels. Do not guess the GitHub owner. Do not open new paid accounts.

## Verify

```
python -m pytest tests/test_crew -q
# 363 passed (2026-08-28)
GET /crew/detect?q=Add%20impeccable%20skill%20into%20skill%20storage
# skills includes skill-ingest, spawn true
```

Live leftover: space `c17e99f632ab` run `9f25df1aed4d` failed. Re-run after a live FreeRoute hop.
