# HX-01 rework: secret hygiene on main, provider registry delegated to OpenVault (#280)

Branch `cursor/hx01-secret-hygiene-ov-705b` off main @ c7469da4. Supersedes the #282 shape (a 20-row Cortex provider registry on the #235 scale branch). Lead ruling: OpenVault is the one vault and owns the provider catalogue; Cortex keeps no second key registry and routes models through OV FreeRoute only.

## Facts that drove the shape

- OpenVault's key env names live in `OpenMW/openmw/openvault/vault/airgpt_keyvault.py` (`PROVIDER_TO_ENV`, `ENV_KEY_TO_PROVIDER`), `free_keys_onboard.py` (`CF_TOKEN_ENV_KEYS`) and `env_ingest.py` (`NON_SECRET_ENV_KEYS`; `known_env_keys()` is their union). OV @ b4d68021 lists 32 secret names.
- No OV endpoint returns that set. `GET /api/keyvault/snapshot` gives one `env_key` per catalogue row with `alt_env_keys: []`, so aliases such as `GEMINI_API_KEY`, `NVIDIA_NIM_API_KEY`, `HUGGING_FACE_HUB_TOKEN` are missing. Scrubbing must also work offline at spawn time. So Cortex keeps a static name list pinned to an OV SHA (`CortexOS/integrations/harness/ov_key_envs.py`).
- OV marks `GH_TOKEN` / `GITHUB_TOKEN` as `custom`, not a model provider. Cortex keeps them in child envs because `gh` needs one.
- OV does not list `XAI_API_KEY`, which Crew still offers. Cortex scrubs it as a Cortex-side extra.
- `direct_providers.py` / `tests/test_env_direct_hardening.py` exist only on #235. Main has no env-direct serving, so nothing to derive and no pinned equality to relax.
- `web_tools.fetch` on main had no SSRF guard; the `public_only` guard existed only on #235. Ported verbatim so the broker force means something.
- `crew/mcp_client.py` is on the #215 dual-write ban list (`tests/test_crew/test_cot_climb.py`); MCP children get the scrub through `freeroute.child_env`.

## Drift check

`tests/harness/test_ov_key_envs.py` AST-reads OV source (never imports it) and compares with the mirror. It needs `OPENVAULT_SRC` or a sibling `../OpenVault`; Cortex CI has neither, so that one test skips there. The synthetic must-fail and the Cortex-side subset checks run everywhere.
