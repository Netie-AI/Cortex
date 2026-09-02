---
keywords: [crew, openvault, freeroute, groq, cursor, claude, tokens, 8020, 8010, 8011]
main_idea: Crew is up on openvault/auto. Live FreeRoute PONG walks Groq gpt-oss-120b. Cursor and Anthropic are not vaulted. Crew engine probe hits Constructor :8010 /api/engine/activity and reports offline while engine :8011 is live. Do not auto-register third-party keys.
models: [cursor-grok-4.6]
workflow: 2026-08-28_crew-freeroute-cursor-gap
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-08-23_crew-openvault-desktop.md
repo: Cortex
date: 2026-08-28
---

# Crew FreeRoute walks Groq; Cursor/Claude absent (2026-08-28)

PREFLIGHT: HIT - openvault-loopback-no-bearer, crew-openvault-desktop, ecosystem-analog-nearness.

## Main idea

OpenVault loopback chat works. Crew `:8020` is listening. The quality failure is not a dead vault. FreeRoute `model=auto` landed on Groq `openai/gpt-oss-120b`. Vaulted hops are Groq, OpenRouter, Google, plus a broken SEA_LION custom key (missing base_url). Cursor and Anthropic snapshot `configured=false`. Crew still probes Constructor `:8010` for engine health, so `engine.ok=false` while `:8011` `/health` is ok.

## Golden rule

> Paste Cursor/Claude into OpenVault. Do not auto-register NVIDIA/Ollama/OmniRoute accounts. Prefer vaulted frontier over free-hop `auto`.

## Live evidence (2026-08-28)

| Surface | Result |
|---|---|
| OV `POST /v1/chat/completions` model=auto | 200, content PONG, model `openai/gpt-oss-120b`, `x_groq` |
| OV ratelimit `crew:probe` free | 40000 tokens remaining (InMemoryBucketStore) |
| OV keys | GROQ, OPENROUTER, GOOGLE enabled; SEA_LION enabled but last_error missing base_url |
| snapshot cursor / anthropic / nvidia | configured=false |
| Crew `GET /crew/health` | ok, active `openvault/auto`, engine ok=false url `:8010` |
| listen | `:5000` OV, `:8010` Constructor, `:8011` engine, `:8020` Crew |

## Do not

- Auto-create Ollama Cloud / NVIDIA NIM / OmniRoute accounts to farm keys.
- Paste secrets into chat. Use OpenVault upsert or Crew Providers.
- Treat Constructor `:8010` as the governed engine. AirGPT already falls back to `:8011`.

## Next (when keys land)

1. Vault `CURSOR_API_KEY` and `ANTHROPIC_API_KEY` via OpenVault.
2. Point Crew `CREW_ENGINE_URL` at `:8011`.
3. Run the 3D landing-page bakeoff on Cursor/Claude, not Groq `auto`.
