# PRD: EPIC-HARNESS-ENT, one enterprise-safe model harness for every API key in Cortex

- **Base:** `claude/cortex-scale-agi-state-fkg2b6` at `7cb9491` (draft PR #235). The three drafts were written at `fa0f8a8`. Since then #270 (ROUTER-2 cheapest-first ladder) and #271 (ROUTER-3 predict-before-spend) have landed on this branch.
- **Status:** final synthesis for founder review. It starts from Draft 2, the one the judges favoured, and grafts in the judges' listed ideas from Drafts 0 and 1. Where the drafts conflicted, the safer option won.
- **How it was made:** read-only. No code was changed and nothing was run to write it.
- **Evidence tags:**
  - **[V]** means the harness map checked it offline.
  - **[P]** means it depends on how a library or service behaves.
  - **[checked]** means I read it in the repo at `7cb9491` for this PRD. That covers:
    - the core import pin (`tests/test_freeroute_core.py:459-505`);
    - how `complete()` handles status codes (`integrations/freeroute.py:82`, `:104-113`, `:1707-1792`) and `_transport()` (`:1203-1209`);
    - the #215 guard sets;
    - `ledger_registry.resolve_ledger`;
    - `agent_task.default_broker` (`:90-97`) and `web_tools.fetch` (`:332`);
    - `hls_compiler.py:5,69-72`;
    - `decision_log.py:78`;
    - the Dockerfile `pip install` lines;
    - the contract `Badge` enum.

---

## 0. State at writing, and the question that started this epic

The request was: *is it running, can we scale to more containers, can separate sessions inherit what was learned, and can Cortex become a fully capable harness for all API keys, safe for enterprise use?* Here are the honest answers.

**State when committed (a9613bd, 2026-09-26 11:30 UTC).** This PRD is a plan; no HX ticket has started. Since the drafts were written:
- #270 and #271 (router lane, R3-B) and #265, #268, #275 (security, R3-A) are merged on the base branch (a9613bd). The "PR #278 merged" dependencies below are satisfied on this branch.
- #268 masking runs inside `freeroute._send`, so every ladder rung is masked; the Crew page sends the operator key (`X-API-Key`, asked once, per tab).
- Four child build sessions ran round 3 in separate containers; that is how "scale to more containers" works today (one integrator session, child sessions per ticket group, knowledge through committed docs). It does not make Cortex itself safe to run as N replicas; see below.

**Adding containers is not safe today.** Each replica keeps its own state:
- **Budgets:** crew `_USAGE` and the workflow `CostLedger` live in process memory.
- **429 cooldowns:** they live in a per-host sqlite route store, and `CORTEX_FREEROUTE_LEARN=0` switches them off.
- **Arming and vault caches:** per process.
- **Kill switch:** covers only the FreeRoute core.

With N replicas, every cap is N times too high, a 429 seen by one replica is ignored by the others, and an env kill needs a restart everywhere. Scale-out becomes safe once three things are true:
- HX-03 (the kernel) has landed;
- HX-04 (shared state on Postgres) has landed;
- `CORTEX_HARNESS_REQUIRE_SHARED=1` is set.

**Sessions inherit knowledge only through files checked into the repo:**
- `CLAUDE.md`;
- `docs/strategy/HANDOFF_SCALE_SESSIONS_2026-09-26.md`;
- this PRD, which HX-01 commits as `docs/strategy/PRD_EPIC_HARNESS_ENT.md`.

A session's scratchpad, chat history and local notes do not carry over. Each ticket in §11 fits one session and names its own files, so parallel sessions do not collide.

**"Full capable for all API keys"** is defined here as operator flows F1-F7 (§2). They work with no code, for every major provider, under both custody modes.

## 1. Problem

Cortex spends provider keys on 11 model-call paths over 5 transport families. The safety controls are attached to paths, not to the call itself.

| Path | Entry | Transport | Key source today |
|---|---|---|---|
| P1 | `/dms/query` -> `dms/l2_generation` -> `freeroute.complete` | T-OV or T-ED | OpenVault `ov_` token, or env under env-direct |
| P2 | `/v1/insights` generate | T-OV or T-ED | same; env-direct refuses relayed callers |
| P3, P4 | crew generative relays through `crew/freeroute.py` (guarded) | core | the caller's own `ov_` bearer |
| P5 | `/api/prompt-harness` | core with `bearer=None` | Cortex's own credential |
| P6 | crew chat through the OpenVault connector in `crew/openvault.py` (guarded) | core with `bearer=None` | Cortex's own credential |
| P7 | crew chat through litellm rows (`crew/llm.py`) | litellm | env, read by litellm |
| P8 | workflow and DAG nodes (`routing/adapters/*`) | litellm | env, filled from the vault into `os.environ` |
| P9 | pack SDK (`packs/dms/generative/brain.py`, `packs/dms/tasks/suggest.py`) | Anthropic SDK | `ANTHROPIC_API_KEY`, read at import |
| P10 | kev on loopback, no key | loopback | none; out of scope |
| P11 | `CortexOS/fabrication/hls_compiler.py:69` [checked] | litellm `acompletion` | `config.api_key or "DUMMY_KEY"` |

**Controls today:**
- **Kill switch, custody and leave gate:** only the FreeRoute core has them (P1-P6). With `CORTEX_FREEROUTE=0`, P7, P8 and P9 keep calling providers [V].
- **RBAC:** missing on P5, P6 and P7. The crew server has no authentication on any route, so under env-direct any local HTTP caller, including a browser page using DNS rebinding, can spend the operator's keys.
- **Audit:** no path writes to the hash-chained ledger. The only spend record is the route store, and `LEARN=0` switches it off.
- **Cost units:** there are three.
  - Streamed crew turns record `cost_usd=None` (`llm.py:609`).
  - Budget counters are not atomic.
  - There is no budget per principal or per key, and no inbound limit outside `/dms/*`.
- **Retries and timeouts:**
  - Crew retries can reach about 8 provider hits for one logical call.
  - P8 has no timeout.
  - P9 uses the SDK's default of 600 s [P].
- **Registries:** there are 7 provider registries, 3 secret-env lists and 4 places that set the OpenVault URL, and they disagree.

**Defects confirmed at HEAD** (IDs are from the harness map):
- **C1:** crew sends `XAI_API_KEY`, `OPENROUTER_API_KEY` or `OPENAI_API_KEY` to `api.cursor.com` for any model name containing "grok" (`crew/llm.py:201-202`) [V].
- **C2:**
  - P5 and P6 get around the env-direct rule that refuses relayed callers.
  - Crew labels env-direct traffic "openvault" (`crew/freeroute.py:159,186,355`) [V].
- **C3:** `workflow_openvault.ensure_provider_keys` writes vault secrets into `os.environ` (`:99`), and that arms env-direct.
- **C4:** on a `TypeError`, `cot_climb._call_runner` retries without the caller's bearer (`cot_climb.py:192-195`).
- **C5:** `crew/openvault.py` `upsert_env_key` has no https or loopback check and no auth, and it echoes the error body.
- **C6:** T-OV follows redirects, which may carry the `ov_` bearer [P]. It also reads unbounded bodies and honours env proxies.
- **C7:** `child_env` leaves `ANTHROPIC_API_KEY`, `NVIDIA_NIM_API_KEY` and 7 other key names in child processes [V]. The crew laptop shell keeps the full host env (`shell.py:157`).
- **C8:** crew `keys.save` loses the NVIDIA key.
- **C13:** the cooldown switches off with `LEARN=0`.
- **C14:** provider error text reaches the executor `error` column, `_describe_failure` and `brain.py:65` without redaction.
- **C16:** `model_router._adapter_for` silently falls back to `self_hosted`.
- **Model can turn off the web-fetch guard:** `agent_task.default_broker` calls `web_tools.WEB_TOOLS[name](**params)` [checked `:95-97`]. `fetch` defaults to `public_only=False` [checked `web_tools.py:332`], so the model can pass `public_only=False` itself.
- **`decision_log` file mode:** `decision_log.py:78` creates its file with mode `0o644` [checked].
- **Supply chain:**
  - The images run a bare `pip install` [checked: `Dockerfile:34`, `Dockerfile.core:29`, `Dockerfile.full:30`].
  - `uv.lock` is stale: it records `netie` 0.1.0 against 2.5.0, and `litellm>=1` against `>=1.84.0`.
  - `litellm>=1.84.0` is a floor with no upper pin.

**Malaysian bank buyers ask for controls this code cannot yet show:**
- key custody held by the bank (RMiT 10.21-10.22);
- deny by default, least privilege and time-bound access (10.53-10.57);
- activity logs kept at least 3 years (10.57(c));
- DLP (Appendix 5);
- the ability to suspend (Appendix 9 2(c));
- telling users that AI is used (Appendix 9 2(e));
- residency and cross-border transfer controls (10.50, PDPA s129);
- evidence for the PDPA 72-hour breach clock;
- a pinned supply chain (the LiteLLM compromise of March 2026).

## 2. Operator day-one flows (acceptance targets)

Every flow works through `/v1/harness/*` with no code. The safety is part of each flow, not a setting someone turns on later.

| # | Operator flow | Safety built in | Requirements |
|---|---|---|---|
| F1 | **Connect keys for any provider.** Choose custody. Env: set `OPENAI_API_KEY`, or mount `OPENAI_API_KEY_FILE` from the secret manager. OpenVault: the key goes to the vault and never lands in Cortex. | Only a `kfp_` fingerprint is ever shown. New env-direct providers need an explicit opt-in. `keys.json` is refused in the enterprise profile. Child processes never inherit a key. | R1.4-R1.9 |
| F2 | **Verify a key.** One request with no content. | The result is stored per fingerprint and written to the ledger. No prompt data is sent. | R11.2 |
| F3 | **Set policy:** model allowlist, regions, retention, budgets, RPM, max_tokens, tools. | Enforce with no approved policy denies everything. A steward or admin proposes and a different admin approves, with before and after values in the ledger. A fallback, ladder rung or pin never leaves the allowlist or region. | R4.1-R4.5, R11.1 |
| F4 | **See spend** by tenant, provider, model, key, path and day. | The view equals the ledger sum exactly. Streamed and retried calls are counted. Budgets hold across N containers. | R8.1-R8.5, R11.5 |
| F5 | **Handle incidents and use the kill switch** at global, path, provider, model, key or principal level. | Incidents open automatically, with `first_event_at` for the PDPA and BNM clocks. A suspension reaches every container within 2 s, including in shadow mode. Resuming needs two admins. | R6.6, R7.1-R7.5 |
| F6 | **Rotate or revoke a key.** Env custody: change the secret; the `_FILE` form rotates with no restart. OpenVault: add the next key, verify it, promote it, then retire the old one. | A revoked fingerprint is refused everywhere, even while the env still holds the key. Adding a key under env custody returns 409 with instructions and stores nothing. | R7.5, R11.3, R11.4 |
| F7 | **Scale out.** Set `CORTEX_HARNESS_DSN` (Postgres) and run N replicas. | Every admission decision reads shared state. `CORTEX_HARNESS_REQUIRE_SHARED=1` refuses enforce on sqlite. | R9.1-R9.4 |

## 3. Goals, non-goals, success metrics

### Goals
- **G1. One provider registry.** Every other registry is derived from it.
- **G2. One gate on every spend path, P1-P9 and P11.** Every provider attempt is admitted before it is sent and settled after. The gate is enforced two ways:
  - at runtime, by an admission token;
  - at build time, by a shrink-only AST check that sees imports inside functions.
- **G3. Both custody modes are first-class.** `env-direct` stays, as the founder directed, and gains a `_FILE` form. `openvault` keeps provider keys out of the process.
- **G4. The ledger records every model call.** Intent is written before the call and the outcome after. No caller can opt out.
- **G5. Atomic budgets, rate limits and a shared breaker** that hold across replicas.
- **G6. Policy**, per deployment tenant: allowlist, residency checked per candidate, tools, DLP in both directions.
- **G7. Scoped suspension and revocation** that every path honours within 2 s.
- **G8. Operator flows F1-F7 with no code.**
- **G9. Customer disclosure** on the answer envelope, through contract minor 1.3.0.
- **G10. Every major provider under env custody:**
  - OpenAI and Azure OpenAI;
  - Anthropic (native wire);
  - Gemini and Vertex;
  - Bedrock;
  - Mistral, Cohere, xAI, DeepSeek, Groq, Cerebras;
  - NVIDIA NIM, OpenRouter, Together, Fireworks, Cursor;
  - local Ollama, vLLM and llama.cpp.
- **G11. Images install from a hash-locked supply chain.**

### Non-goals
- **Not duplicated here:**
  - #265 route auth; #275 anonymous generate spend; #268 PII masker (all in PR #278);
  - #270 ladder and #271 predict-before-spend (landed);
  - #269 route store, `pick` and scoring; #272 arming and `served_*`.
- **No second key store.** OpenVault stays the store, and env-direct only reads.
- **No probabilistic injection or jailbreak classifier.** `CortexOS/execution/manifest.py` and its corpus are untouched.
- **No response cache, semantic or exact.** Stamps carry `cache: "off"`.
- **One tenant per deployment** (FD-7). Multi-tenant deployments are a follow-up epic.
- **Out of scope:**
  - SSO, SAML and SCIM;
  - notifying regulators automatically;
  - a SIEM exporter beyond JSONL export;
  - content logging (`full` is refused in v1; FD-9).
- **No change to `canonical_manifest_bytes()`,** no contract major, and no edit to any #215-banned file (§5.4, FD-2 to FD-4).
- **No deletion of the litellm paths or crew streaming** in this epic (FD-5).
- **No claim of RMiT compliance.** Cortex supplies controls and evidence; the bank decides.
- **No DMS repo changes.** DMS picks up contract 1.3.0 in its own lane, over HTTP.

### Success metrics
Measured in CI unless marked otherwise.
- Every debt file for the egress chokepoint is empty for crew, workflow, packs and fabrication. `other.txt` holds only guarded-file entries, each tied to an FD.
- Ledger parity: the number of `model_call.outcome` events equals sends plus refusals, including with `LEARN=0`.
- Budget overshoot under concurrency: 0.
- A suspension or revocation reaches every replica within 2 s.
- The spend view equals the ledger sum with 0 difference.
- Kill switch: 0 provider invocations on every path.
- **Rollout gate, not CI:** the curated 52-question run keeps WRONG = 0 and answered >= 43 in shadow and in enforce. It needs live keys; with the Gemini free tier at about 20 requests a day, it may have to be reported as unmeasured.
- **Manual:** from a blank deploy to the first governed answer in 15 minutes or less, with no code.

## 4. Requirements

Each requirement is a check CI can run. Every test uses a stubbed transport, with no live key and no network. A skipped test counts as failing.

### R1 Registry and custody
- **R1.1** `CortexOS/integrations/harness/registry.py` (stdlib only) is the only provider table. Each row has:
  - `id` and `aliases`;
  - `key_envs`, primary first, plus aliases such as `NVIDIA_NIM_API_KEY`;
  - `auth` in {bearer, x-api-key, azure-api-key, bedrock-bearer, sigv4, gcp-oauth, none};
  - a fixed https `base_url`. Local rows are loopback only. Azure takes its base URL from env only if it is https and the host matches `*.openai.azure.com` or `*.cognitiveservices.azure.com`;
  - `wire` in {openai_compat, anthropic, bedrock_converse, vertex};
  - the litellm prefix and `default_model`;
  - `region` and `retention`, both defaulting to `unknown`;
  - the allowed custody modes;
  - a price in micro-USD per million tokens, in and out, with `as_of` and `source`, or none;
  - `shipped`.
- **R1.2** A model id resolves by its **exact** provider prefix, never by substring. An unknown prefix raises `UnknownProvider`; nothing falls back to `self_hosted`.
  - *Test:*
    - `xai/grok-4` goes to api.x.ai with the xAI key;
    - `openrouter/x-ai/grok-4` goes to openrouter.ai;
    - `openai/grok-4.6` goes to api.openai.com;
    - `cursor/*` sends only `CURSOR_API_KEY`.

    Fails today (C1).
- **R1.3** These registries are derived from the registry: `direct_providers.PROVIDERS`, crew `keys.KEY_ENVS` and `KNOWN`, the crew config chain, `connectors._API_ROWS`, `workflow_openvault._KEY_ENV`, and the Mistral default model.
  - A drift test fails if any registry outside the guarded files disagrees. Today crew uses `mistral-small-latest` and env-direct uses `mistral-medium-latest`.
- **R1.4** `secret_env_names()` is the only secret list. It includes:
  - every provider key env and its `<ENV>_FILE` form;
  - `CORTEX_FREEROUTE_TOKEN`, `AWS_BEARER_TOKEN_BEDROCK` and `GMAIL_APP_PASSWORD`.

  It is applied like this:
  - `direct_providers.KEY_ENVS` equals this list, so `freeroute.child_env` strips all of them with no edit to `freeroute.py`, and so do `mcp_client` and `app_runner`, which use `child_env`.
  - The crew shell (laptop and isolate) and `gh` get `scrub(os.environ)`. `GH_TOKEN` is kept for `gh`.
  - A dev-only `CREW_SHELL_KEEP_PROVIDER_KEYS=1` exists, and the enterprise profile refuses it.
  - *Test:* 20 or more planted dummy secrets never reach a child. Fails today (C7, `shell.py:157`).
- **R1.5** Env-direct serves a newly catalogued row only when its id is listed in `CORTEX_DIRECT_PROVIDERS`. The existing four providers keep their order, defaults and behaviour.
  - *Test:* an `OPENAI_API_KEY` set for crew does not arm env-direct for openai.
- **R1.6** Custody is latched once per process from `CORTEX_MODEL_TRANSPORT`: `openvault` (the default) or `env-direct`. Every ledger event records it.
  - Under `openvault` in enforce, no path reads a provider key from env:
    - P7 env rows and P8 env lookups refuse with `custody_mismatch`;
    - P8 sends through the core, so keys stay in OpenVault.
  - Under `env-direct`, a key is read per call from env or `<ENV>_FILE`, with a file cache of at most 5 s. It is never written to `os.environ` and is sent only in the header the row declares, never in the URL.
- **R1.7** Nothing writes a provider secret into `os.environ`, in any mode.
  - *Test:* `os.environ` is byte-identical before and after a stubbed workflow run against a stub vault that returns secrets. Fails today (C3).
- **R1.8** A key fingerprint is `kfp_` + HMAC-SHA256(install_salt, key)[:16 hex]. It is the only value derived from a key that may appear anywhere.
  - `install_salt` is created once in the harness state store, which all replicas share on Postgres, or read from `CORTEX_HARNESS_FP_SALT_FILE`. It is never logged.
  - *Test:* two store instances on one DSN give the same fingerprint.
  - *Leak scan:* after a stubbed run over every path, no ledger payload, stamp, log line, `node_executions.error` value, transcript or API response contains any substring of 6 or more characters from any dummy key.
- **R1.9** Crew key management:
  - `keys.save` refuses to upsert to a vault that is neither https nor loopback. It makes 0 HTTP requests, and the error contains no key.
  - It keeps its local copy unless the vault can serve the key back (the NVIDIA case, C8).
  - `keys.json` has mode 0600.
  - The enterprise profile refuses `keys.json` as a key source.

### R2 Gate and chokepoint
- **R2.1 Placement.** The harness lives under `CortexOS/integrations/harness/`.
  - Any module that `integrations/freeroute.py` imports is stdlib-only at import time. `CortexOS.audit`, SQLAlchemy and the optional cloud libraries are imported inside functions.
  - The harness never imports `packs.*`, `CortexOS.crew` or `duckdb`.
  - *Reason:* `tests/test_freeroute_core.py::test_core_imports_only_stdlib_integrations_and_paths` [checked] pins `freeroute.py` to the stdlib, `CortexOS.integrations*` and `CortexOS.paths`. It walks the AST, so it sees imports inside functions too, and it scans `CortexOS/integrations`, `dms` and `api` for crew imports. That test file is banned by #215, so a harness at `CortexOS/harness/` would break CI with no allowed fix.
- **R2.2 One gate API.**
  - `gate.wrap_transport(send, impl, own_credential_fp)` for the core.
  - `gate.call(intent)`, a sync and async context manager, for litellm, SDK and adapter sites.
  - `gate.tool_egress(...)` for web tools and AWS Comprehend.

  Every provider attempt Cortex sends is admitted before and settled after. That includes each retry, each ladder rung and each fallback candidate.
- **R2.3 Stages are auto-discovered** from `harness/stages/*.py` and sorted by `ORDER`. A ticket adds its own stage file and owns its DDL; it never edits `gate.py`. The first failure wins, and each has a stable reason code:

  | ORDER | Stage | Ticket | Reason codes |
  |---|---|---|---|
  | 00 | profile | HX-09 | `profile_not_ready:<item>` |
  | 02 | state_shared | HX-04 | `state_not_shared`, `state_unavailable` |
  | 05 | kill | HX-03 | `harness_killed`, `suspended:<scope>`, `key_revoked` |
  | 10 | principal | HX-03 | `no_principal`, `tenant_mismatch`, `credential_downgrade` |
  | 20 | custody | HX-05 | `custody_mismatch` |
  | 25 | context_scope | HX-05 | `cross_tenant_context` |
  | 30 | policy | HX-05 | `not_allowed:<path/provider/model/region/retention/max_tokens>`, `residency_unknown` |
  | 35 | egress_pii | HX-05 | `unmasked_pii_at_egress` |
  | 50 | rate | HX-04 | `rate_limited` |
  | 60 | breaker | HX-04 | `breaker_open` |
  | 70 | budget | HX-04 | `budget_exhausted`, `price_unknown` |
  | 90 | audit_intent | HX-03 | `audit_unavailable` |

  After the response, the output guard (HX-05) inspects it. Settle then records budget, breaker, the outcome event and incident observers (HX-09).

  The gate itself uses two more codes:
  - `harness_unavailable` for any stage exception;
  - `policy_invalid` in enforce when the policy is missing or invalid.
- **R2.4 Runtime enforcement.** Admission mints a single-use token in a ContextVar, bound to `call_id`.
  - The env-direct transport consumes it: first `direct_providers.request_json` (HX-03), then `harness/transport/http.send` (HX-06).
  - With no token, or a reused token, enforce returns a `harness_bypass` refusal and opens 0 sockets. Shadow records `would_refuse=harness_bypass`.
- **R2.5 Build-time enforcement.** `tests/invariants/test_model_egress_chokepoint.py` is a protected path, landed in HX-02 with `INVARIANT-CHANGE:`. It scans `CortexOS/**` and `packs/**`, but not tests.
  - **It fails when:**
    - (a) a reference to a provider completion entry point sits outside a `with` or `async with` block whose context expression calls `CortexOS.integrations.harness.gate.call`. Entry points are litellm `completion`/`acompletion`/`text_completion`, `messages.create`, `chat.completions.create`, `responses.create`, `generate_content`, and Bedrock `converse`/`invoke_model`. A reference counts whether it is an attribute or an imported name, and whether it is at module level or inside a function;
    - (b) a provider host literal appears outside the files exempt by design.
  - **Exempt by design:** `CortexOS/integrations/harness/transport/`, `harness/registry.py` and `integrations/direct_providers.py`.
  - **Debt:** exact repo-relative paths in `tests/invariants/egress_debt/{crew,workflow,packs,fabrication,other}.txt`. The union must stay a subset of `_DEBT_CEILING`, which is frozen in the test, so debt can only shrink. Each migration ticket deletes only its own file's lines.
  - **Meta-tests:**
    - these plants must fail: an ungated `import litellm; litellm.completion(...)` inside a function, an alias `f = litellm.acompletion`, a host literal, and a debt path not in the ceiling;
    - a gated plant must pass.
- **R2.6 End state:** the crew, workflow, packs and fabrication debt files are empty.

### R3 Principal and ingress
- **R3.1** In enforce, no principal means no spend. A principal has an actor, a role of viewer or higher, a tenant, a key fingerprint and a surface. Principals are bound in these places:
  - `auth_port.require_role` for engine routes, including `/dms/query`, `/v1/insights` and the workflow run requester;
  - crew binds the #265 principal;
  - service principals `svc:<name>` are declared in policy for in-process jobs;
  - workflow nodes inherit the requester's principal;
  - a **relay principal** `ov:<fp>` covers P3 and P4. It applies when the bearer the core sends belongs to the caller, not to Cortex; the gate compares fingerprints against `own_credential_fp`.

  `bearer=None` with nothing bound is `no_principal`.
  - *Test:* P5 and P6 with no principal in enforce make 0 transport calls. Fails today.
- **R3.2** The principal survives thread hops. Crew `run_core` copies contextvars [V].
  - *Test:* the ledger actor equals the authenticated caller on `/dms/query` and `/v1/insights`.
- **R3.3** One tenant per deployment. The policy declares `tenant_id`, and every event carries it.
  - A principal whose authorizer reports a different tenant is refused with `tenant_mismatch`.
- **R3.4** A call that started with a caller's relayed credential is never re-sent on Cortex's own credential. `cot_climb._call_runner` refuses on a `TypeError` instead.
  - *Test:* exactly 1 attempt, and 0 sends on Cortex's credential. Fails today (C4).
- **R3.5** Crew ingress hardening, layered on #265 rather than duplicating it:
  - a `Host` header allowlist, where a foreign `Host` gets 421 and makes 0 calls;
  - a non-loopback `--host` with no authorizer makes the server exit non-zero;
  - the prompt-harness `candidates` pin needs steward, and its candidates must be inside the allowlist.

### R4 Policy and residency
- **R4.1 The policy file.** A YAML file (`CORTEX_HARNESS_POLICY`), versioned by sha256. Unknown keys are refused. Its sections:
  - `tenant_id` and `service_principals`;
  - `models`: `provider:model` globs per path;
  - `regions`, `retention` and `custody`;
  - `fallback.max_attempts`, default 3;
  - `deadline_s`: default 60, maximum 180;
  - `max_tokens`;
  - `limits`, each in tokens and micro-USD per day: per principal, per key fingerprint, per run and per tenant, plus `rpm`;
  - `prices` overrides;
  - `network_tools` in {deny, allow}, default deny in enforce;
  - `egress_pii` in {record, refuse};
  - `content_logging` in {none, hash}, default none;
  - `unpriced_model` in {refuse, charge_at_cap};
  - `openvault.attested_regions`.

  The dev default policy reproduces today's behaviour. The enterprise default denies everything.
- **R4.2** Each candidate is admitted on its own. A fallback, ladder rung or pin never leaves the allowlist.
  - *Test:* allowed candidate A returns 429, and disallowed candidate B gets 0 sends. Fails today, because `pick()` has no allowlist.
- **R4.3** When the policy lists regions or retention values, `unknown` is not allowed.
- **R4.4 OpenVault custody.** Cortex cannot see which hop OpenVault serves from.
  - A region-restricted policy refuses OpenVault sends with `residency_unknown`, unless the operator attests OpenVault's hop regions in the policy.
  - After a response, if #272's `served_provider` names a provider outside the allowlist:
    - the answer is withheld (guard verdict `residency_breach`);
    - an incident opens.
- **R4.5 Prompt context is scoped to the tenant.**
  - Few-shot pairs and promotions get an additive, nullable `tenant` column.
  - `sql_generator` selects only the bound tenant's pairs and tags the context it sends.
  - The policy stage refuses any tagged context from another tenant with `cross_tenant_context`.
  - Legacy NULL rows are served only when `tenant_id` is `default`, until `scripts/harness_adopt_context.py --tenant <id>` stamps them and writes one ledger event.
- **R4.6 Tool egress.**
  - In enforce, `web_fetch` and `web_search` are denied unless `network_tools: allow`.
  - Each fetch appends a `tool_egress` event with host, status and bytes, never the query text.
  - The agent broker forces `public_only=True` whatever the params say.
    - *Test:* with `public_only=False` in the params, a loopback, private or link-local target gets 0 fetches. Fails today.
  - AWS Comprehend (`pii_ner.py`) is tool egress too: it honours the kill switch and region policy and is written to the ledger.

### R5 DLP and guards
- **R5.1** Masking reuses #268 `pii_mask`; there is no second masker. `mask(mask(x)) == mask(x)`, and restoring gives back the original.
- **R5.2** Before sending, the gate runs the #268 detector over the outbound body.
  - With `egress_pii: refuse` (the enterprise default), any unmasked identifier gives `unmasked_pii_at_egress` and 0 bytes are sent.
  - Otherwise the hit is recorded.
- **R5.3** The output guard runs after the response and before the caller sees it:
  - an NRIC, card or account number in the output that was not in the input is masked (`redacted_output`);
  - tool calls outside the path's tool allowlist are dropped (`tool_dropped`);
  - `finish_reason=length` is marked `truncated`;
  - R4.4 applies (`residency_breach`).
- **R5.4** Error text goes through `harness.secrets.redact_secrets`, which covers key shapes and PII (C14). This applies to transcripts, the executor `error` column, pack errors and `_describe_failure`.
- **R5.5** Every file the harness writes has mode 0600. `decision_log.py:78` changes from 0o644 to 0o600.

### R6 Audit and incidents
- **R6.1 What is written.** Events go through `resolve_ledger()`, which is imported lazily. It imports the active pack on first use, so the crew server and workers reach the same ledger.
  - Every admitted attempt appends `model_call.intent` after all checks and before the transport.
  - Every attempt, admitted or refused, appends `model_call.outcome`. The outcome holds:
    - call_id, logical_call_id, attempt, path, tenant_id, actor, role and surface;
    - custody, provider, requested_model, served_model, served_provider, region and key_fp;
    - masked counts by kind;
    - tokens in and out, usage_estimated, cost_usd_micro and latency_ms;
    - outcome, reason_code, guard verdicts, policy_hash, mode and profile;
    - `prompt_sha256` of the masked body, only when `content_logging: hash`.

  Events never hold prompt or completion text, or key material. Recording does not depend on `LEARN`, and a `no_log`-style parameter is never honoured.
- **R6.2 No audit, no spend.**
  - If the intent append fails in enforce, the call is refused with `audit_unavailable` and the transport is never called.
  - If the outcome append fails, the event goes to a 0600 JSONL spool beside the harness state. The spool is drained in order on the next success.
  - `/v1/harness/ready` reports the backlog, and returns 503 in enforce above the threshold (default: 100 events, or the oldest older than 5 min).
- **R6.3 Parity.** Across the full stubbed suite, the count of outcome events equals sends plus refusals, and the chain verifies.
- **R6.4** Admin actions go to the ledger with the actor and the before and after values: suspend, resume request and approve, policy propose and approve, key verify, rotate and revoke, and incident acknowledge and close.
- **R6.5 Export.** Admins export JSONL by sequence range, with chain verification.
  - No harness or ledger-provider code issues `DELETE` or `UPDATE` on ledger rows (AST test).
  - Keeping records 3 years is storage policy, covered in the runbook.
- **R6.6 Incidents open automatically for:**
  - a provider 401 or 403 on a key;
  - a breaker open for more than 10 min;
  - a budget at 80% (warning) or 100%;
  - a spend spike: more than 3 times the trailing hourly mean and more than $1;
  - a stale key or price older than 90 days;
  - the audit being unavailable;
  - a kill switch engaged;
  - a masking refusal;
  - a residency breach.

  Each incident records `opened_at`, `first_event_at` (the earliest triggering event, for the PDPA 72 h and BNM clocks), its scope and ledger sequence references. Acknowledging and closing need an admin and are written to the ledger.

### R7 Kill switch, suspension, revocation
- **R7.1** `CORTEX_FREEROUTE=0` stops P1-P9 and P11 before any network I/O, in every mode including `off`.
  - *Test per path:* 0 transport or SDK invocations. Fails today for P7, P8 and P9 [V].
- **R7.2** Runtime suspension scopes are `all`, `path`, `provider`, `model`, `key_fp` and `principal`. One admin can suspend, and it takes effect immediately. Every replica honours it within 2 s; tests inject a clock.
- **R7.3** Resuming needs maker-checker:
  - admin A requests and a different admin B approves within 15 min;
  - the same admin gets 409, and a late approval gets 410;
  - a viewer or steward gets 403, no key gets 401, and no authorizer gets 503.
- **R7.4** Suspensions, revocations and the env kill are enforced in shadow mode too.
- **R7.5** A revoked `key_fp` is refused on every replica within 2 s, even while the env still holds the key.

### R8 Limits and resilience
- **R8.1** One unit: `cost_usd_micro` (int) and tokens. The price comes from the registry or a policy override.
  - When a USD limit is set and the policy says `refuse`, an unpriced model is refused with `price_unknown`.
- **R8.2 Atomic reservations.** The estimate is prompt tokens plus `max_tokens`, times the price. It is reserved against tenant, principal, key_fp, run and provider, per day.
  - *Test:* 16 processes each reserve 1k tokens against a 10k cap on shared sqlite; exactly 10 are admitted.
  - *Test:* 2 processes make 50 reserves against room for 30 on Postgres; exactly 30 are granted.
- **R8.3** Settle uses actual usage.
  - Unknown usage, such as a streamed turn with no usage block, is charged the full reservation, never 0 or None.
  - A crashed replica's reservation is released after the deadline plus 60 s.
- **R8.4** A caller-supplied ceiling, such as the workflow `/run` ceiling, can only lower the policy ceiling. A ceiling is never unlimited.
- **R8.5** RPM limits per principal apply on every spend path, including `/v1/insights` and crew.
- **R8.6** Provider health is shared across replicas and does not depend on `LEARN`.
  - A 429, 402 or 503 opens a breaker keyed by provider and key_fp. It honours `Retry-After`, capped at 24 h.
  - A refused send returns 429 `harness_breaker_open`, so the core's own cooldown agrees.
  - There is no edit to `_cooling()` or to the route store that #269 owns.
  - *Test:* process A sees a 429, and process B sends 0 inside the window with `LEARN=0`. Fails today (C13).
- **R8.7** A logical call makes at most `fallback.max_attempts` provider attempts (default 3), counted across every layer. Crew sets `num_retries=0`.
- **R8.8 Deadlines.** The default is 60 s and the maximum 180 s.
  - The transport timeout is the smaller of the caller's timeout and the remaining deadline.
  - Adapters and litellm get an explicit `timeout`. Nothing uses an SDK's 600 s default.

### R9 Scale
- **R9.1** `HarnessState` is one port with three implementations:
  - memory, for tests;
  - sqlite, the default for one host: `BEGIN IMMEDIATE`, WAL, with the file beside `CORTEX_FREEROUTE_SCOREBOARD` so tests stay in `tmp_path`;
  - Postgres, set by `CORTEX_HARNESS_DSN`: `SELECT ... FOR UPDATE`, with SQLAlchemy imported lazily.

  It holds suspensions, revocations, the install salt, budgets, rate windows, breakers, approvals, incidents and key verification results.
- **R9.2** No harness decision relies on a per-process cache older than 2 s.
- **R9.3** With `CORTEX_HARNESS_REQUIRE_SHARED=1`, enforce refuses on sqlite or memory with `state_not_shared`. `/ready` returns 503 when the store is unreachable in enforce.
- **R9.4** Across hosts, the ledger uses the existing Postgres ledger (`DMS_LEDGER_DSN`), which rls.yml already covers with concurrent-append tests [checked]. The new Postgres tests are added to rls.yml `--expect`, so they cannot pass by being skipped.

### R10 Providers and wires
- **R10.1** `harness/transport/http.send` is one hardened stdlib client:
  - no redirects, so Authorization never reaches a 3xx target;
  - an 8 MB body bound, TLS verification on, env proxies ignored, and a deadline;
  - it consumes the admission token.
- **R10.2 Wires:**
  - **openai_compat:** OpenAI, Azure OpenAI (with `api-version` and the host restriction), the Gemini OpenAI-compatible endpoint, Mistral, Groq, xAI, DeepSeek, Cerebras, NVIDIA NIM, OpenRouter, Together, Fireworks, Cohere's compatibility API [P], Cursor [P], and loopback-only Ollama, vLLM and llama.cpp;
  - **anthropic:** the native Messages API [P];
  - **bedrock_converse:** `AWS_BEARER_TOKEN_BEDROCK` [P], or SigV4 through the optional `[cloud]` extra (FD-10);
  - **vertex:** through the optional `[cloud]` extra, imported lazily [P].

  A row that is not yet wired returns `adapter_not_shipped`, which is not scored.
- **R10.3** Every wire is tested against recorded fixtures, with a socket guard that fails any connection off loopback.

### R11 Operator surface
- **R11.1 Routes.** `/v1/harness/*` covers:
  - status, ready, providers, spend and incidents;
  - keys: verify, add, rotate and revoke;
  - policy: propose and approve;
  - suspend, resume (request and approve), and audit export.

  These are not contract routes, so the OpenAPI export allowlist is unaffected [checked].

  Roles:
  - reads need viewer;
  - proposing and verifying need steward;
  - approving, killing, revoking and adding need admin.

  A viewer gets 403 on every mutating route, with the store and ledger unchanged. An unauthenticated caller gets 401 or 503, with no side effect. No response contains key material (the R1.8 scan).
- **R11.2 Verify** sends one request with no content per fingerprint.
  - Env custody: `GET /models` on the provider host, through the hardened client.
  - OpenVault custody: the existing arming read.

  The result is stored per fingerprint and written to the ledger.
- **R11.3** Adding a key under env custody returns 409 naming the env or `_FILE` variable, and stores nothing.
- **R11.4 Rotation.**
  - Under env custody, a fingerprint change appends `harness.key.rotated{old_fp,new_fp}`.
  - Under OpenVault custody: add the next key, verify it, promote it, then retire the old key after a grace period (default 24 h).
- **R11.5** The spend view breaks down by tenant, provider, model, key_fp, path and day. It equals the ledger outcome sum for the same window exactly; a mismatch opens an incident.
- **R11.6** The runbook `docs/HARNESS_OPERATOR.md` covers day one, rotation, the kill drill, incident export, Postgres sizing and retention.

### R12 Customer disclosure (contract minor 1.3.0)
- **R12.1** `Answer.model_disclosure: ModelDisclosure | None = None`, with these fields:
  - `ai_used`, `custody`, `provider`, `requested_model`, `served_model`, `served_provider`, `region`;
  - `attempts`, `fallback_count`, `degraded`, `reason`;
  - `guard` verdicts, `truncated`, `harness_mode`, `cache: "off"`;
  - `audit_ids`, the ledger sequence numbers.

  It holds no key material.
- **R12.2** A refusal, abstention, truncation, or any guard verdict other than pass never carries a green badge.
  - `provenance.badge` is `abstain`, or `blocked` for a policy block; both are existing values of `Badge` [checked].
  - The answer shows abstention prose and no rows.
  - Tests assert the rendered answer text and the rows (CLAUDE.md §8).
  - A governed-metric answer has `ai_used` false.
- **R12.3** A shadow run is never presented as governed: the disclosure says `harness_mode: shadow`.

### R13 Supply chain
- **R13.1** Images install with `--require-hashes` from a lock file per image, then install the project with `--no-deps`.
- **R13.2** A stale lock fails the build. The project version and every specifier must match `pyproject.toml`, and `uv.lock` is regenerated.
- **R13.3** `litellm` 1.82.7 and 1.82.8 are denylisted.
- **R13.4** `litellm` stays a base dependency with its floor pin, but images get exact, hashed versions. Removing it is FD-5. Cloud auth libraries appear only in the optional `[cloud]` extra, exact-pinned.

### R14 Enterprise profile
- **R14.1** With `CORTEX_HARNESS_PROFILE=enterprise`, every call refuses with `profile_not_ready:<item>` unless all of these hold:
  - the mode is enforce;
  - the state is shared Postgres;
  - a ledger is registered and reachable;
  - an explicit policy is active, not the dev default;
  - `keys.json` is absent;
  - the crew shell opt-out is unset;
  - unpriced models are refused;
  - the install salt is present;
  - the spool backlog is below its threshold.

  The enterprise profile refuses mode `off`. `/v1/harness/status` lists missing items without their values.

### R15 Modes and fail-closed
- **R15.1** `CORTEX_HARNESS=off|shadow|enforce` is latched at first read. A later env write cannot change it, and status reports the latched value.
- **R15.2 off:** `_transport()` returns the raw pair, byte-identical to today. Only R7.1 still applies to every path.
- **R15.3 shadow:**
  - Every stage runs, and each refusal is recorded as `would_refuse=<reason>`.
  - Budgets reserve and settle but never block.
  - Suspension, revocation and kill are enforced.
  - Shadow never relaxes a refusal that exists today: the env-direct relay refusal, the leave gate, `CORTEX_FREEROUTE=0`, #268 masking, and #265 and #275 auth.
- **R15.4 enforce:** any of these refuses with a stable code, and nothing partial is sent:
  - a stage exception;
  - an unreadable store;
  - an invalid or missing policy;
  - a missing authorizer.

  Certified and deterministic answers keep serving.
- **R15.5 How refusals look on the core path** [checked].
  - Scoped to a tenant or principal:
    - The gate returns `(HARNESS_REFUSED_STATUS, {"error":{"type":"harness_<reason>","message":...}})`. `HARNESS_REFUSED_STATUS` is a dedicated constant (proposed 451) added to `_STATUS_REASONS` as "Cortex harness refused".
    - Being in `_STATUS_REASONS` means it is not scored and it stops the #270 ladder.
    - It is not in `COOLDOWN_STATUSES` = {429, 402, 503} (`:82`), so it does not cool the provider for anyone else.
    - It is never 401, because on the OpenVault wire a 401 calls `note_rejected` on Cortex's own credential (`:1786-1788`).
    - It is never 403, because on the OpenVault wire a 403 clears `_arming_cache` and `_vault_cache` (`:1789-1792`).
  - Scoped to a provider: breaker refusals return 429 `harness_breaker_open`.
  - The gate returns refusals as values and never raises into `complete()`. A transport exception becomes status 0, and status 0 is scored against the model (`:1718-1719`, `:1784`).
  - The one edit inside `complete()` is the refusal-branch `base` expression (`:1775-1779`). It reads "Cortex harness refused" when the error type starts with `harness_`, so no stamp says "OpenVault refused" or "env-direct provider answered".

## 5. Architecture

```
ingress, binds a Principal into harness.context (ContextVar)
  engine: auth_port.require_role / authorize_and_bind
  crew:   #265 principal + Host allowlist middleware (run_core copies context into its pool)
  relay:  caller's own ov_ bearer -> ov:<fp>      jobs: svc:<name> from policy

call sites                                  gate hook (one per transport family)
  P1-P6 core ....... integrations/freeroute._transport() -> gate.wrap_transport(send, impl, own_fp)
  P7 crew litellm .. crew/llm.chat: async with gate.call(intent) around each attempt
  P8 workflow ...... routing/adapters/*: gate.call per attempt; openvault custody -> adapters/core.py (core relay)
  P9 packs, P11 .... -> CortexOS.integrations.freeroute.complete (no SDK of their own)
  tools ............ web_tools, pii_ner -> gate.tool_egress

gate.admit(intent): stages from harness/stages/*.py sorted by ORDER (table in R2.3)
  -> Admission{call_id, token, reservation, deadline_s, attempt}
  -> audit_intent (ORDER 90): ledger append or refuse audit_unavailable
transport: T-OV (unchanged, guarded) | harness/transport/http.send (token required) | litellm (explicit key, timeout)
inspect: output guard (DLP, tool allowlist, truncation, residency_breach)
settle: budget.settle (unknown usage = full) -> breaker.record -> model_call.outcome (spool on failure) -> observers (incidents)

state:  harness/state.py port -> memory | state_sqlite.py | state_postgres.py (CORTEX_HARNESS_DSN)
audit:  CortexOS.audit.resolve_ledger() (lazy) -> pack ledger (Postgres via DMS_LEDGER_DSN when multi-host)
```

### 5.1 Modules
All live under `CortexOS/integrations/harness/`. Modules the core imports are stdlib-only at import time.

| Module | Ticket | Holds |
|---|---|---|
| `registry.py`, `secrets.py` | HX-01 | provider rows, resolution, `secret_env_names`, `scrub`, `redact_secrets`, `read_key` (env and `_FILE`) |
| `context.py`, `mode.py`, `policy.py`, `gate.py`, `admission.py`, `audit.py`, `state.py`, `state_sqlite.py`, `fingerprint.py`, `stages/{kill,principal,audit_intent}.py` | HX-03 | the kernel |
| `budget.py`, `pricing.py`, `rate.py`, `breaker.py`, `state_postgres.py`, `stages/{state_shared,rate,breaker,budget}.py` | HX-04 | limits and shared state |
| `guard.py`, `stages/{custody,context_scope,policy,egress_pii,output_guard}.py` | HX-05 | policy and guards |
| `transport/{http,openai_compat,anthropic,azure,bedrock,vertex}.py` | HX-06 | wires |
| `approvals.py`, `incidents.py`, `keys_admin.py`, `spend.py`, `profile.py`, `stages/{profile,incidents}.py` | HX-09 | operator plane |

### 5.2 Core wiring, and who owns which lane
HX-03 edits three things in `integrations/freeroute.py`:
- `_transport()`: wraps both the override pair and the real pair.
- `_STATUS_REASONS`: gains one entry.
- the refusal-branch `base` expression.

Nothing else in the file changes: not `pick`, not the store schema, not `_write_row`, `_stats` or `_cooling()` (#269), and not arming or `served_*` (#272). The HX-03 PR lands after #278 merges.

### 5.3 Custody matrix (enforce)

| Path | `openvault` | `env-direct` |
|---|---|---|
| P1, P2, P9, P11 (core) | `ov_` token or loopback; provider secrets never in the process | env or `_FILE` read per call over T-ED; relayed callers refused (existing rule) |
| P3, P4 | the caller's own `ov_` bearer is relayed; principal `ov:<fp>` | relayed callers refused (existing rule) |
| P5, P6 | need a bound principal (#265 plus HX-07) | same |
| P7 crew litellm | env rows refused (`custody_mismatch`); only the OpenVault connector serves | env rows allowed; key passed explicitly per row |
| P8 workflow | nodes relay through the core (`adapters/core.py`); keys stay in OpenVault | key read per call and passed as `api_key=`; vault to env filling refused |

### 5.4 Guarded files

| File | Problem | Plan |
|---|---|---|
| `crew/freeroute.py` | hard-codes custody "openvault" (`:159,186,355`) | FD-2. Until then, `/v1/harness/status` is the source of truth. P3 and P4 are gated at the core transport. |
| `crew/openvault.py` | secret upserts with no https or loopback check and no auth; echoes `resp.text[:200]`; httpx honours env | FD-3. Until then, HX-01 pre-checks in `crew/keys.save`, the enterprise profile refuses crew key upserts, and P6 is gated at the core transport. |
| `integrations/openvault_client.py` | T-OV follows redirects [P], reads unbounded bodies, honours proxies | FD-4. There is no safe workaround outside the file. |
| `crew/mcp_client.py` | inherits keys | Fixed through `child_env` (HX-01); no edit. |
| `crew/server.py` | allowed, but the `freeroute_complete` and `freeroute_status` handlers and `CALLER_KEY_RULE` must stay intact | HX-07 adds middleware and a startup check only. |
| `tests/conftest.py`, `tests/freeroute_fake.py`, `tests/test_freeroute_core.py`, `tests/test_crew/test_{freeroute,openvault}.py`, `packages/cortex_contract/execution.py`, `execution/distill_harness.py`, `crew/liberty_*` | banned | Untouched. Fixtures go in `tests/harness/conftest.py` and per-area conftests. |

## 6. Rollout: flags, shadow first

### Flags

| Flag | Values | Notes |
|---|---|---|
| `CORTEX_HARNESS` | `off`, `shadow`, `enforce` | Latched at first read. Ships as `off`; flipping the default is FD-6. |
| `CORTEX_HARNESS_PROFILE` | `dev`, `enterprise` | `enterprise` refuses `off` and anything not ready. |
| `CORTEX_HARNESS_POLICY` | path to YAML | |
| `CORTEX_HARNESS_DSN` | Postgres DSN | Unset means sqlite beside the scoreboard. |
| `CORTEX_HARNESS_REQUIRE_SHARED` | `0` or `1` | `1` refuses enforce on sqlite. |
| `CORTEX_HARNESS_FP_SALT_FILE` | path | Optional salt source. |
| `CORTEX_DIRECT_PROVIDERS` | list of provider ids | Opt-in for new env-direct rows; unset means the current four. |
| `<ENV>_FILE` | path | Mounted-secret custody that rotates with no restart. |
| `CORTEX_MODEL_TRANSPORT`, `CORTEX_FREEROUTE=0` | unchanged | `CORTEX_FREEROUTE=0` now stops every path. |

### Phases

| Phase | Tickets | Exit criteria |
|---|---|---|
| 0: plumbing | HX-01, HX-02, then HX-03 in `off` | Full suite green. The only behaviour changes are stricter scrubbing, the `keys.save` https check, the `decision_log` mode, the broker's forced `public_only`, the `cot_climb` refusal, hash-locked images, and `CORTEX_FREEROUTE=0` covering every path. |
| 1: shadow | HX-04 to HX-08 and HX-10 merged; default flips to `shadow` (FD-6) | Ledger parity 100%. Zero unexplained `would_refuse` on authenticated traffic for 7 days or 500 calls, whichever is longer. Spend view equals the ledger sum. Admit plus settle p95 <= 50 ms on sqlite, including both ledger appends (bench, informational). Curated 52: WRONG 0, answered >= 43, or reported as unmeasured. |
| 2: enforce, path by path | HX-09 merged | Engine P1 and P2 first, then P8, then P9 and P11, then crew P3-P7. A tabletop kill drill (`scripts/harness_kill_drill.py`): suspend all, then resume under maker-checker, recorded in the ledger and signed per FD-6. |
| 3: enterprise | `CORTEX_HARNESS_PROFILE=enterprise`, `REQUIRE_SHARED=1` | `/v1/harness/status` shows no missing items. DMS pins cortex-contract 1.3.0 and `assert_envelope_valid` checks `model_disclosure` (DMS lane). |

**Rollback:** restart with `CORTEX_HARNESS=shadow`. Kill switches keep working in shadow, and the latch stops a downgrade while the process runs. The ledger is never switched off.

### Parallel seating

| Wave | Tickets | Condition |
|---|---|---|
| 1 | HX-01, HX-02 | independent; can start now |
| 2 | HX-03 | after HX-01 and after #278 merges |
| 3 | HX-04, HX-05, HX-06, HX-07, HX-08 (optionally 08a and 08b), HX-10 | after HX-03; files are disjoint (§11) |
| 4 | HX-09 | after HX-04, HX-05 and HX-06 |

## 7. Risks

| Risk | Mitigation |
|---|---|
| Lane collisions in `integrations/freeroute.py` (#269, #272, #278) | HX-03 edits only `_transport()`, one `_STATUS_REASONS` entry and one `base` expression, after #278 merges. No other ticket touches the file. |
| A ContextVar set in a sync FastAPI dependency runs in a threadpool copy and may not reach the endpoint [P], giving false `no_principal` | Shadow first. HX-03's actor test settles it. If needed, bind in an async dependency or in middleware. |
| Two ledger appends per call on a serialised hash chain | Measured in phase 1. If too slow, it becomes a founder decision. The intent is never silently dropped. |
| A store outage becomes a model outage (fail-closed) | By design. Postgres HA, a named 503, and certified answers keep serving. |
| Unknown region or retention makes enforce refuse everything | By design (deny by default). The bank attests values in policy. OpenRouter has no Malaysian region. Bedrock ap-southeast-5 may route outside Malaysia through inference profiles [unverified]. |
| OpenVault's own fallback is invisible to Cortex | `residency_unknown` unless attested, plus the `served_provider` check after the call (R4.4). |
| A harness refusal stops the #270 ladder instead of stepping to an allowed rung | Fails closed. Follow-up for the #270 owner: pre-filter rungs by `policy.allowed()`. |
| Regex DLP misses bare names (#268 `NAME_RULE_ONLY`) | Disclosed in `masked` counts and in the Appendix 7 pack. |
| The egress PII check flags SQL literals | `record` in dev, shadow first, detector tuned before enterprise sets `refuse`. |
| litellm as a supply-chain target (March 2026) | HX-02 hash lock and denylist; long term FD-5. |
| Keys held in the process env | `_FILE` custody, R1.4 scrubbing, OpenVault recommended for banks (FD-8), a "keys in process env" line on status. |
| Stale price table gives wrong budgets | `as_of` on every price, an incident after 90 days, token limits as an alternative, `price_unknown`. |
| Maker-checker slows an emergency resume | Suspending needs one admin. A dev-only `CORTEX_HARNESS_SINGLE_ADMIN=1` exists, and the enterprise profile refuses it. |
| Shadow mode read as governance | The disclosure says shadow, and there is no badge change. |
| Test hermeticity once the default leaves `off` (`tests/conftest.py` is guarded) | Harness sqlite and the spool live beside `CORTEX_FREEROUTE_SCOREBOARD` [Draft 1: conftest points it at `tmp_path`; HX-03 confirms]. The flip PR adds a check that the repo `data/` is unchanged after the suite (FD-6). |
| Guarded-file gaps stay open | FD-2, FD-3, FD-4. |
| The contract minor needs DMS coordination | Additive only; 1.0.0, 1.1.0 and 1.2.0 stay byte-identical. |

## 8. Founder decisions

| # | Decision | Recommendation |
|---|---|---|
| FD-1 | #267 KEYS-OV-ONLY conflicts with env-direct. | Re-scope #267 to two points: (a) `openvault` custody means OpenVault only, which R1.6 enforces; (b) Cortex never writes provider keys to env or `keys.json` in any mode. Env-direct reading stays as the operator's latched option. |
| FD-2 | `crew/freeroute.py` (banned) hard-codes custody "openvault" at `:159,186,355`. | Allow a label-only diff that reads the core's custody. Until then, point the crew UI at `/v1/harness/status`. |
| FD-3 | `crew/openvault.py` (banned). | Allow a diff covering: https or loopback plus the `ov_` bearer on `upsert_env_key`, `list_vault_keys`, `arm_source`, `check_gate` and the snapshot read; no error-body echo; `trust_env=False`; GEMINI and NVIDIA mappings. The #215 lane owns it. |
| FD-4 | `integrations/openvault_client.py` (banned): T-OV redirects, unbounded body, proxies. | Allow a diff that refuses redirects, bounds the body and bypasses proxies, the same way T-ED does. The alternative is to approve moving the core's OpenVault chat sends onto `harness/transport/http` in a follow-up. This is the highest priority for banks. |
| FD-5 | litellm long term. | Keep it hash-locked and gated in this epic (the default here). Removing it from the base install means deleting the adapters and the crew litellm path, and dropping crew token streaming unless an incremental guard is built. Decide after phase 2. |
| FD-6 | Rollout: flip the default from `off` to `shadow` (touches guarded conftest hermeticity), the enforce date per path, and who signs the kill drill. | Flip after wave 3, with the "repo `data/` unchanged" check. Enforce in the order given in §6. |
| FD-7 | Tenancy. | Confirm one tenant per deployment for this epic. Multi-tenant work (a `role@tenant` key format in `packs/dms/security/api_auth.py`, per-tenant policy, RLS `tenant_id`) becomes a follow-up epic coordinated with #265. |
| FD-8 | Custody default and leave gate for the enterprise profile. This changes custody defaults. | OpenVault as the enterprise default, with env allowed per policy (RMiT 10.21(a), 10.22). Env-direct gets `leave_gate: required` in enterprise; dev keeps today's skip. |
| FD-9 | Content logging and currency. | v1 refuses `content_logging: full`; it needs an encrypted content store. Budgets are stored in micro-USD and tokens. MYR display at a pinned FX rate is a follow-up. |
| FD-10 | Cloud wires: add the optional `[cloud]` extra (botocore signer, google-auth, exact-pinned and hashed). | Approve it. If declined, Bedrock ships bearer-only, and Bedrock SigV4 and Vertex stay `adapter_not_shipped`. |

## 9. Coordination with work in flight

| Item | Relationship |
|---|---|
| #278 (#265, #268, #275) | Must merge before HX-03, HX-05, HX-07, HX-08b and HX-10. Masking runs before the gate, and the gate counts `<PII:...>` placeholders. |
| #265 | Owns route auth for `/v1/contract/*`, crew and `/dms/query`. The harness is a second check at the transport. HX-07 adds a Host allowlist and a startup refusal on top; it does not duplicate #265's auth. |
| #275 | Owns anonymous insights generate spend. HX-03 only binds the principal in `_env_direct_caller_ok`. |
| #270 (landed) | Each ladder rung is one send, so it gets one admission. A harness refusal stops the ladder. |
| #271 (landed) | A predict-before-spend skip means no send, so no admission. |
| #269 | Owns the route store, `pick` and scoring. Harness refusals are not scored. The shared cooldown lives in the harness breaker, not in `_cooling()`. |
| #272 | Owns arming and `served_*`. The guard (R4.4) and disclosure read `served_*`. |
| #263 T2-CTRL-B | Owns `workflow_routes.py` and `dag_run.py`. HX-08 does not touch them. |
| #267 | FD-1. |

## 10. Control to regulation map (for the Appendix 7 pack)

| Control | RMiT / PDPA (as cited in the research; the bank confirms) | Requirements |
|---|---|---|
| Bank-held keys, key-compromise plan, rotation | 10.20-10.23; App 10 §8 | R1.6-R1.9, R7.5, R11.2-R11.4 |
| Deny by default, least privilege, maker-checker | 10.53-10.57 | R3.1-R3.5, R4.1, R7.3, R11.1, R15.4 |
| Activity logs kept 3 years or more, tamper-evident | 10.57(c) | R6.1-R6.5 |
| DLP in both directions | App 5 | R1.4, R4.6, R5.1-R5.5 |
| Residency and cross-border transfer | 10.50; PDPA s129 | R4.2-R4.4 |
| Suspend and monitor | App 9 2(c), 2(d) | R6.6, R7.1-R7.5, R11.1 |
| Tell users AI is used | App 9 2(e) | R12.1-R12.3 |
| Concentration risk and exit | App 10 §5, §7 | R8.6, R10.2 |
| Third party and supply chain | App 8 | R13.1-R13.4 |
| Breach timelines (PDPA 72 h, BNM) | PDPA breach guideline; App 12 | R6.6 (`first_event_at`), R6.5 (who, what and when, by key or principal) |

## 11. Tickets

### Shared rules for every ticket
- **Start:**
  - Read `CLAUDE.md`, the handoff doc and this PRD.
  - Branch from the tip of `claude/cortex-scale-agi-state-fkg2b6` and open a draft PR against it.
  - Run `git log --oneline -3` before any amend or rebase, and never rewrite another lane's commit.
  - Stage exact paths only; this checkout has #278's files staged.
- **Tests:**
  - Every test listed as "must fail today" must fail on the base. Prove it by stashing the production change.
  - A skipped test counts as failing. Postgres tests are deselected by marker in the default job and listed in rls.yml `--expect`.
  - Fixtures:
    - `tests/harness/conftest.py` is created by HX-03 and frozen after wave 2.
    - Every other ticket uses its own `tests/harness/<area>/conftest.py`.
    - Never `tests/conftest.py`.
  - Answer-path tests assert the rendered answer text, the rows, the badge and `audit_id`. SQL assertions may only be added on top of these.
- **Protected paths and guards:**
  - A commit touching `tests/invariants/**` carries `INVARIANT-CHANGE:` in its body. Debt files may only shrink.
  - Do not edit any #215-banned file. If a pinned behaviour must change, stop and raise it as a founder decision.
- **Gates:**
  - `python -m pytest tests/ -q`;
  - `ruff`;
  - `mypy`;
  - `lint-imports` (the console script);
  - `python scripts/check_versions.py`;
  - `python scripts/export_openapi.py --check`;
  - both #215 guard tests and `tests/test_freeroute_core.py`, unchanged;
  - `python -m pytest tests/test_execution/ -q` whenever an execution file changes.

### Ticket map

| Key | Title | Wave | Depends on |
|---|---|---|---|
| HX-01 | Provider registry, secret hygiene, quick safety fixes | 1 | none |
| HX-02 | Build gates: hash-locked images and the egress chokepoint invariant | 1 | none |
| HX-03 | Harness kernel and core wiring (P1-P6), answer path | 2 | HX-01, #278 |
| HX-04 | Budgets, pricing, rate, shared breaker, Postgres state | 3 | HX-03 |
| HX-05 | Policy, residency, custody, egress PII, output guard, tenant-scoped context | 3 | HX-03, #278 |
| HX-06 | Hardened transport and provider wires | 3 | HX-01, HX-03 |
| HX-07 | Crew ingress and egress (P5, P6, P7) | 3 | HX-02, HX-03, #278 |
| HX-08 | Workflow (P8), packs (P9), HLS (P11), tool egress | 3 | HX-02, HX-03, #278 (08b) |
| HX-10 | Customer disclosure, contract 1.3.0 | 3 | HX-03, #278 |
| HX-09 | Operator plane, maker-checker, incidents, enterprise profile | 4 | HX-04, HX-05, HX-06 |

Within wave 3, file sets are disjoint:
- **HX-04:** limits modules and rls.yml.
- **HX-05:** guard and policy stages, plus the three pack few-shot files.
- **HX-06:** `transport/`, `direct_providers.py`, `registry.py`, and the root `pyproject.toml` and locks.
- **HX-07:** `crew/*` and `egress_debt/crew.txt`.
- **HX-08:** `execution/*`, `routing/adapters/*`, the pack SDK files, `fabrication/*`, and `egress_debt/{workflow,packs,fabrication}.txt`.
- **HX-10:** `packages/cortex_contract/*`, `contract/*`, `api/contract_routes.py` and `dms/answer_engine.py`.

## 12. Not verified in this pass
- Whether a ContextVar set in a FastAPI dependency reaches sync endpoints. HX-03's actor test settles it.
- Whether the pack ledger restricts `event_type` values. HX-03 checks. If it does, adding the types in `packs/` is allowed.
- How the Anthropic Messages API, Cohere's compatibility API, the Cursor API, the Bedrock bearer token and Vertex behave [P].
- That `tests/conftest.py` points `CORTEX_FREEROUTE_SCOREBOARD` at `tmp_path` (claimed by Draft 1; HX-03 confirms).
- Bedrock ap-southeast-5 inference-profile routing.
- The curated 52-question run needs live keys and may be unmeasured on the Gemini free tier.
