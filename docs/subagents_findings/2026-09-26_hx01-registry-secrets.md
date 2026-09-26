# HX-01: one provider registry, one secret list, quick safety fixes (#280)

Branch `claude/cortex-hx-01` from `claude/cortex-scale-agi-state-fkg2b6` @ 03b545f. PRD: `docs/strategy/PRD_EPIC_HARNESS_ENT.md` (R1.1-R1.9, R3.4, R4.6, R5.4, R5.5).

## Expected vs actual

| Requirement | Actual on 03b545f | Now |
|---|---|---|
| R1.1-R1.3: one provider table; others derived | 7 registries. `direct_providers.PROVIDERS`, crew `keys.KEY_ENVS` / `KNOWN` hand-written | `CortexOS/integrations/harness/registry.py` (stdlib only, 20 rows). `direct_providers` and crew `keys` derive from it; drift tests fail when they disagree |
| R1.2: exact-prefix resolution | No resolver; crew routes by substring (C1, `crew/llm.py`) | `registry.resolve()`; unknown prefix raises `UnknownProvider`. **Crew is not rewired yet** (`crew/llm.py` is HX-07) |
| R1.4: no child inherits a secret | `child_env` stripped 5 names; `ANTHROPIC_API_KEY`, `NVIDIA_NIM_API_KEY`, `OPENAI_API_KEY`... reached `mcp_client` / `app_runner` children. Crew laptop shell and `gh` got the full host env (`shell.py:157`) | `direct_providers.KEY_ENVS == secret_env_names()` (every registry key env, each `_FILE` form, the OpenVault token, Bedrock bearer, Gmail password), so `freeroute.child_env` strips all of them with no edit to `freeroute.py`. Laptop shell and `gh` get `scrub(os.environ)`; `GH_TOKEN` kept. Dev opt-out `CREW_SHELL_KEEP_PROVIDER_KEYS=1` (laptop only) |
| R1.5: new env-direct rows need opt-in | n/a (only 4 rows) | `CORTEX_DIRECT_PROVIDERS=openai,...` adds shipped openai_compat rows after the 4 defaults; latched with the transport opt-in |
| R1.6: `_FILE` custody | Env only | `read_key`: env value, then `<ENV>_FILE` (cache <= 5 s, rotates with no restart); never written to `os.environ` |
| R1.9: crew `keys.save` | Upserted to any vault URL (http to a remote host too). NVIDIA key dropped locally after a vault "ok" the vault cannot serve back (C8). `keys.json` written then chmod'd | Refuses non-https / non-loopback vault with 0 requests. Keeps the local copy unless the vault maps the name. Written 0600 from the first byte (tmp + replace) |
| R3.4: `cot_climb` TypeError | Retried without the caller's bearer (C4): 2 attempts, the second on Cortex's credential | 1 attempt, named refusal |
| R4.6: broker `public_only` | Model could pass `public_only=False` to `web_fetch` | Broker forces `public_only=True`; loopback / private / link-local / metadata get 0 fetches |
| R5.5: decision log mode | Created 0644 | Created 0600; an existing 0644 log is tightened on next write |

## Repro

```
python -m pytest tests/harness -q -p no:cacheprovider
```

- **Whole production diff stashed** (`git stash push -u -- CortexOS/`): `test_hx01_registry.py` and `test_hx01_secrets.py` fail to collect (`cannot import name 'registry'`); exit 2.
- **Only the wiring stashed** (harness package present, every edit to `crew/`, `decision/`, `execution/`, `direct_providers.py` stashed): 21 failed, 41 passed, exit 1. The 21 are every behavioural test above. The 41 that pass are tests of the new modules alone (which fail to import on base) and positive controls: `save` still upserts to https/loopback vaults, an `OPENAI_API_KEY` alone does not arm env-direct, `keys.json` ends 0600 (base already chmod'd after writing; HX-01 closes the 0644 window, which no test observes).
- **Fixed:** 62 passed.
- **Live** (evidence, n=1 each, real env keys, values never printed): env-direct armed from the registry-derived table; `google:gemini-3-flash-preview` and `nvidia:moonshotai/kimi-k3` both answered "OK" (HTTP 200). `freeroute.child_env()` held no provider key. At `max_tokens=20` both returned empty text (reasoning budget), same as the ROUTER-2 finding.

## Conflict found and how it was resolved

`tests/test_env_direct_hardening.py::test_child_env_strips_every_provider_key_env` asserted `set(direct_providers.KEY_ENVS) == {5 names}`. PRD R1.4 and the ticket require `KEY_ENVS == secret_env_names()` with no edit to `freeroute.py`, and the brief also said to keep that test unchanged. Both cannot hold. The equality became `ALL_KEY_ENVS <= KEY_ENVS` (one line); the rest of the test (`child_env(src) == {PATH, HOME}`) is untouched, so every one of the 5 names is still proven stripped. Exact equality with `secret_env_names()` is asserted in `tests/harness/test_hx01_registry.py`.

## Root-cause class

**Parallel hand-maintained lists of the same fact** (provider key names, hosts, defaults). Each list was right when written; the next provider added to one was not added to the others, so a child scrub, a crew form or a transport silently disagreed. Plus **fallback that drops a protection** (C4 retry without the bearer, broker letting the model pick its own SSRF guard, vault upsert with no scheme check).

## Invariants that apply

- Fix the root-cause class, not the symptom: one registry, everything else derived, a drift test that fails.
- Silent fallback is a lie: C4 now refuses by name. **Gap:** an unknown or unshipped id in `CORTEX_DIRECT_PROVIDERS` (e.g. `anthropic`) is dropped without a message, because the arming reason lives in `freeroute.py`, which HX-01 does not edit. HX-03 or HX-09 status should name ignored ids.
- A skipped test is a failing test: no new test skips on Linux CI (two POSIX file-mode tests skip on Windows only).
- A control that blocks legitimate work is itself a failure: https and loopback vaults still get upserts; `gh` keeps `GH_TOKEN`; the four env-direct defaults keep their order, models and live behaviour.

## Not done here (belongs to later tickets)

- Crew litellm routing by exact prefix (C1) and crew config / connectors / `workflow_openvault` derivation: HX-07 / HX-08. The drift test only asserts their names are a subset of the registry.
- Enterprise-profile refusal of `keys.json` and of `CREW_SHELL_KEEP_PROVIDER_KEYS`: HX-09.
- `redact_secrets` exists but no call site uses it yet (R5.4 call sites are HX-03+).
- Registry prices are all `None` (no verified price with a source); region and retention are `unknown`.
- env-direct nvidia now also reads `NVIDIA_NIM_API_KEY` as a fallback (the registry alias), matching crew.
