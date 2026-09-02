# 2026-08-28 -- OpenVault loopback rejects dummy bearers

Keywords: openvault, loopback, bearer, 401, openvault-loopback, agent_task, litellm

Main idea: Loopback `POST /v1/chat/completions` is unmetered only with no Authorization. LiteLLM `api_key=openvault-loopback` (or EMPTY) 401s. AGENT_TASK now uses OpenVaultAdapter (httpx, no dummy bearer) plus native tool_calls. Constructor `:8010` is not the engine; AirGPT falls back to `:8011`.
