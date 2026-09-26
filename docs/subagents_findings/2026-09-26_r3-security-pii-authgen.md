# R3 security: PII masking at every outbound model call (#268), no anonymous generate spend on /v1/insights (#275), contract auth (#265)

Branch `claude/cortex-r3-security`, base `claude/cortex-scale-agi-state-fkg2b6` at `fa0f8a8`. One commit per issue.

## #268 PII-MASK-FREEROUTE

### Expected vs actual (base fa0f8a8)
- Expected: no model call leaves the process carrying an IC, phone, email, account number or person name, on the OpenVault transport, the env-direct transport and the Crew litellm path; a masker failure refuses the call.
- Actual: `CortexOS/integrations/freeroute.complete()` put the caller's `messages` into the request body unchanged. `CortexOS/crew/llm.chat()` handed `messages` to `litellm.acompletion` unchanged. `redact_port` only ran on the DAG executor path (`invoke_routed_completion`), not on either of these. A question such as `which supplier uses procurement1@supplier001.example.com? asked by Encik Tan Ah Kow, IC 900101-14-5678` reached the stand-in provider verbatim.

### Repro
`python -m pytest tests/security/test_pii_mask_268.py -q -p no:cacheprovider` with `CortexOS/integrations/freeroute.py` and `CortexOS/crew/llm.py` at `fa0f8a8`: 8 failed + 1 answer-path failure (the masker-only unit cases pass there because the new module exists; on the true base the file fails at import).

### Root-cause class
Missing control at a choke point: a protection existed (redact port) but was wired to one caller path, not to the transport choke points every model call crosses.

### Fix
- `CortexOS/integrations/pii_mask.py` (new): reversible masking with stable per-call placeholders (`<PII:IC_1>`, `<PII:EMAIL_1>`, ...). Reuses the redact-port patterns (MyKad, NRIC, card, email) and adds MY/E.164 phone, 10-16 digit account runs, honorific-led and patronymic (`bin`/`binti`/`a/l`/`a/p`) names. Then runs the redact port's second pass, so a pack-registered redactor still applies. A registered span detector (NER) plugs in via `register_span_detector`. Any error raises `MaskingFailed`. It lives under `CortexOS/integrations/` because `tests/test_freeroute_core.py` (a #215-guarded file) pins `freeroute.py` to stdlib + `CortexOS.integrations` imports.
- `freeroute.complete()`: masks before `pick()`/send; a masker error returns a stamped refusal (`pii_masking_failed`) with nothing sent. The reply `message` (content and tool-call arguments) is restored locally before `accept()` and SQL extraction. `RouteStamp.masked` holds counts by kind only. `MASKING_STATE` is now `"on"`. This covers OpenVault and env-direct, since both transports sit below this line.
- `crew/llm.chat()` (founder override for #215): same mask before litellm, `LLMError` on masker failure with no send, restore of text, reasoning and tool-call args. Streaming chunks are restored per chunk, best effort.
- `freeroute_baseline.comparable_with_masking_on` now follows `MASKING_STATE`.

### Invariants
- "Never cut on trust-boundary validation / security"; "Silent fallback is a lie": names without a cue are not caught without a registered NER. `name_coverage()` reports `name rule only (no name detector registered)`. Not yet surfaced on the envelope (leftover, below).
- "Assert the artifact the customer receives": the gen-ask-sql test asserts the rendered answer text, the rows and `sql_used` after restore, not only the outbound body.

### Leftovers (not done here)
- Bare names with no honorific or patronymic cue (`orders for Tan Ah Kow`) pass unmasked unless a pack registers an NER span detector. Presidio is not wired as the default detector in this change.
- `name_coverage()` is not stamped on `RouteStamp`.
- Luhn is not applied to CARD (the redact-port pattern is reused as is), so some digit groups (year lists) over-mask. They are restored in the reply, so the answer is unaffected, but the model sees placeholders.
- The Crew `openvault/` connector goes through `crew/openvault.py` -> `crew/freeroute.complete_core` -> `freeroute.complete`, so it is covered. `CortexOS/routing/adapters/*` already redact through `invoke_routed_completion`, one way (no restore).

## #275 ROUTER-1a / AUTH-GEN-01 (OpenVault path)

### Expected vs actual (base fa0f8a8)
- Expected: `POST /v1/insights` with `generate=true` and no caller `ov_` bearer is refused before any provider call, loopback included, with a named reason. Cortex's armed key is never lent. A 401 body holds no path and no learn/setup state. Authenticated bodies hold no filesystem path.
- Actual:
  - A loopback caller (`127.0.0.1`, no Authorization) with FreeRoute armed was served on the loopback tier. `relay_bearer` returned `""`, and the stand-in provider got the think and generate calls.
  - `Authorization: Bearer ` (empty) and `X-API-Key` only were served the same way.
  - The 401 body carried `route_store.path` (a server file path), `learn_enabled`/`learn_source` and the rest of the fingerprint.
  - Authenticated bodies carried `route_store.path` too.

### Repro
`python -m pytest tests/dms/test_insights_auth_gen_275.py -q -p no:cacheprovider` with `CortexOS/insights/routes.py` at `fa0f8a8`: 6 of 6 fail. Base answered `status: ABSTAIN, phase: generate` with provider calls for the anonymous loopback caller.

### Root-cause class
Fail-open credential default: "no credential presented" was mapped to "unattributed loopback tier" instead of "refuse". Plus a response serialiser that exposed internal setup state to unauthenticated callers.

### Fix (`CortexOS/insights/routes.py` only; `integrations/freeroute.py` untouched by this commit)
- Only a caller's own `ov_` bearer may spend on the OpenVault path. With FreeRoute armed, anything else gets a 401 with `reason: generate_requires_bearer` and zero provider calls.
- With FreeRoute unarmed, the anonymous generate still runs. It gets the unarmed refusal envelope, as before, so DMS's unarmed ranking flow is unchanged. It runs through `_anonymous_complete`, which never passes `bearer=None` (Cortex's key) and refuses if FreeRoute arms mid-request.
- The env-direct refusal carries `reason: generate_requires_auth`.
- The 401 body is reason code + message only.
- `stamp_api` drops `route_store.path`. `route_store_id` and `learn_*` stay for authenticated callers.
- Item 9: `freeroute_baseline.row_credential` / `row_countable` put the credential on every row and never count an unattributed or missing credential.

### Tests changed (requirement change, disclosed)
- `tests/dms/test_insights_router_fingerprint.py::test_http_insights_and_401_carry_fingerprint`: the 401 half used to assert the fingerprint was present. #275 acceptance 1 reverses that, so it now asserts the reason code and the absence of the fingerprint.
- `tests/test_freeroute_router1.py` and `test_insights_router_fingerprint.py`: `masking_state` is `"on"` (the #268 flip).

### Invariant
"A control that blocks legitimate work is itself a failure": ranking without generation for the same anonymous loopback caller still returns CERTIFIED with rows and answer text (asserted).

### Leftovers
- The seeded-ontology pin: Cortex already drops the `ontology` field at `fa0f8a8`, so that assertion is a pin and holds on base. The test fails on base only through its path assertion.
- Item 8 (key id / `verified_by` stamp) is #276, per the issue comments.

## #265 TRUST-CONTRACT-AUTH

### Expected vs actual (base fa0f8a8)
- Expected:
  - Every `/v1/contract/*` route refuses a keyless or under-privileged caller (401/403) with no side effect.
  - `ledger/append` records the authenticated caller.
  - Crew and `/dms/query` never spend a model credential for an unauthenticated caller, loopback included.
- Actual:
  - The contract routes had no auth dependency at all.
  - `ledger/append` wrote `body.actor`, so a caller could write rows as "admin".
  - Crew message/spawn/accept/assign/tickets and `POST /crew/insights` (generate) served a keyless loopback caller. The insights generate call spent, stamped `loopback tier (unattributed)`.

### Repro
Check out `CortexOS/api/contract_routes.py`, `CortexOS/api/dms_query.py`, `CortexOS/crew/server.py` and `CortexOS/crew/prompt_harness_routes.py` at `fa0f8a8`. Then `python -m pytest tests/security/test_contract_auth_265.py tests/test_crew/test_crew_spend_auth_265.py -q -p no:cacheprovider` gives 25 failed. At head the same run gives 25 passed.

### Root-cause class
Missing authorization at a trust boundary, and caller identity taken from the request body instead of the credential.

### Fix
- Role gates through the TRUST-01 port:
  - viewer: ask, drillthrough, tools, ledger/verify;
  - steward: submit, ledger/append, jwks/refresh.
- `ledger/append` takes `Principal.actor`. The body `actor` wire field is kept and ignored, because removing it would be a contract major.
- `auth_port.require_spend_auth()` names the refusal `spend_requires_auth`. It gates `/dms/query`, the crew routes that start a model call, and `/crew/insights` when `generate` is true.

### Open (needs a founder decision)
- `POST /crew/freeroute` is still ungated. The #215-guarded `tests/test_crew/test_freeroute.py` pins a keyless loopback spend there, and an `ov_` relay that the auth port cannot admit until OpenVault#67 ships.
- **Deploy risk (a control that blocks work):**
  - The DMS key that calls Cortex must now be steward+ for submit, ledger/append and jwks/refresh.
  - The Crew UI sends no key today, so crew chat, spawn and tickets refuse until it does.
  - A crew process without an authorizer (`PACK` not dms) answers 503 on those routes.
- The `/dms/query` 401/403 `detail` is now an object (`code`, `message`, `auth`) instead of a string. The status codes are unchanged.

### Invariant
"Fix the root-cause class": actor from the credential, never the body. "Never cut on trust-boundary validation".

### Subagent record
One worktree agent on #265 (isolated context, general-purpose). #268 and #275 were done in the main session. The agent's worktree started at 27f79ea and it reset to fa0f8a8 before working. Its commit was cherry-picked cleanly (`83e50c8`) and re-verified here: 25 tests fail on the base routes, and the full suite gives 3246 passed.
